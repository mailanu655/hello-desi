"""
Mira — Stripe Webhook Handler

Handles Stripe payment events to automatically activate/deactivate
business subscriptions when customers pay or cancel.

Endpoint:
  POST /api/v1/stripe/webhook — Stripe webhook receiver

Events handled:
  - checkout.session.completed   → Activate paid plan
  - customer.subscription.updated → Handle plan changes
  - customer.subscription.deleted → Downgrade to free
  - invoice.payment_failed       → Notify owner of payment failure
"""

import logging

import stripe
from fastapi import APIRouter, HTTPException, Request
from config.settings import get_settings

router = APIRouter()
logger = logging.getLogger(__name__)


@router.post("/stripe/webhook")
async def stripe_webhook(request: Request):
    """
    Receive and process Stripe webhook events.

    Stripe signs every webhook with the endpoint secret.
    We verify the signature before processing any event.
    """
    settings = get_settings()

    if not settings.STRIPE_WEBHOOK_SECRET:
        logger.error("STRIPE_WEBHOOK_SECRET not configured — rejecting webhook")
        raise HTTPException(status_code=500, detail="Webhook secret not configured")

    # Read raw body for signature verification
    payload = await request.body()
    sig_header = request.headers.get("stripe-signature", "")

    try:
        event = stripe.Webhook.construct_event(
            payload, sig_header, settings.STRIPE_WEBHOOK_SECRET
        )
    except ValueError:
        logger.warning("Stripe webhook: invalid payload")
        raise HTTPException(status_code=400, detail="Invalid payload")
    except stripe.error.SignatureVerificationError:
        logger.warning("Stripe webhook: invalid signature")
        raise HTTPException(status_code=400, detail="Invalid signature")

    event_type = event["type"]
    data = event["data"]["object"]

    logger.info(f"Stripe webhook received: {event_type} (id={event.get('id', '?')})")

    # ── Route events ─────────────────────────────────────────────
    try:
        if event_type == "checkout.session.completed":
            await _handle_checkout_completed(data, settings)

        elif event_type == "customer.subscription.updated":
            await _handle_subscription_updated(data, settings)

        elif event_type == "customer.subscription.deleted":
            await _handle_subscription_deleted(data, settings)

        elif event_type == "invoice.payment_failed":
            await _handle_payment_failed(data, settings)

        else:
            logger.info(f"Stripe webhook: unhandled event type {event_type}")

    except Exception as e:
        logger.error(f"Stripe webhook handler error for {event_type}: {e}")
        # Return 200 anyway so Stripe doesn't retry indefinitely
        return {"status": "error", "message": str(e)}

    return {"status": "ok"}


# ══════════════════════════════════════════════════════════════════
# Event handlers
# ══════════════════════════════════════════════════════════════════

async def _handle_checkout_completed(session: dict, settings):
    """
    A customer completed a Stripe Checkout session (payment link).

    We use the customer email or metadata to find the business,
    then activate their subscription.
    """
    from supabase import create_client

    customer_email = session.get("customer_email") or session.get("customer_details", {}).get("email", "")
    customer_name = session.get("customer_details", {}).get("name", "")
    subscription_id = session.get("subscription")
    amount_total = session.get("amount_total", 0)  # in cents

    logger.info(
        f"Checkout completed: email={customer_email}, "
        f"name={customer_name}, sub={subscription_id}, "
        f"amount={amount_total}"
    )

    # Determine which plan based on amount
    plan = _amount_to_plan(amount_total)

    if not plan:
        logger.warning(f"Could not determine plan from amount {amount_total}")
        return

    # Store the Stripe subscription in our subscriptions table
    # We'll match by customer_email → business owner's email or wa_id
    client = create_client(settings.SUPABASE_URL, settings.SUPABASE_KEY)

    # Try to find a pending/active subscription that matches
    # First try by Stripe customer email → user_state (if they registered with email)
    # For now, log the payment and mark any matching pending subscription as paid
    if subscription_id:
        # Update any existing subscription with this Stripe subscription ID
        existing = (
            client.table("subscriptions")
            .select("*")
            .eq("stripe_subscription_id", subscription_id)
            .limit(1)
            .execute()
        )

        if existing.data:
            client.table("subscriptions").update({
                "status": "active",
                "stripe_status": "active",
                "plan": plan,
            }).eq("stripe_subscription_id", subscription_id).execute()
            logger.info(f"Updated existing subscription {subscription_id} → {plan}")
            return

    # Log the payment for manual reconciliation if no match found
    logger.info(
        f"Stripe payment logged (no auto-match): "
        f"email={customer_email}, plan={plan}, stripe_sub={subscription_id}"
    )

    # Store payment event for tracking
    try:
        import uuid
        client.table("stripe_events").insert({
            "id": str(uuid.uuid4()),
            "event_type": "checkout.session.completed",
            "stripe_subscription_id": subscription_id or "",
            "customer_email": customer_email or "",
            "customer_name": customer_name or "",
            "plan": plan,
            "amount_cents": amount_total,
            "raw_data": {
                "session_id": session.get("id"),
                "payment_status": session.get("payment_status"),
            },
        }).execute()
    except Exception as e:
        logger.warning(f"Failed to log stripe event: {e}")


async def _handle_subscription_updated(subscription: dict, settings):
    """Handle subscription plan changes (upgrade/downgrade)."""
    from supabase import create_client

    stripe_sub_id = subscription.get("id")
    status = subscription.get("status")  # active, past_due, canceled, etc.
    items = subscription.get("items", {}).get("data", [])
    amount = items[0].get("price", {}).get("unit_amount", 0) if items else 0
    plan = _amount_to_plan(amount)

    logger.info(f"Subscription updated: {stripe_sub_id} → status={status}, plan={plan}")

    client = create_client(settings.SUPABASE_URL, settings.SUPABASE_KEY)

    # Find and update matching subscription
    result = (
        client.table("subscriptions")
        .select("*")
        .eq("stripe_subscription_id", stripe_sub_id)
        .limit(1)
        .execute()
    )

    if result.data:
        sub = result.data[0]
        updates = {"stripe_status": status}

        if plan:
            updates["plan"] = plan

        if status in ("active", "trialing"):
            updates["status"] = "active"
        elif status in ("past_due", "unpaid"):
            updates["status"] = "active"  # Keep active but flag
            logger.warning(f"Subscription {stripe_sub_id} is {status}")
        elif status == "canceled":
            updates["status"] = "canceled"
            updates["plan"] = "free"
            # Also remove featured badge
            if sub.get("business_id"):
                client.table("businesses").update({
                    "is_featured": False
                }).eq("id", sub["business_id"]).execute()

        client.table("subscriptions").update(updates).eq(
            "stripe_subscription_id", stripe_sub_id
        ).execute()

        # Notify business owner via WhatsApp
        if sub.get("wa_id") and status == "active" and plan:
            from app.services.whatsapp_service import WhatsAppService
            whatsapp = WhatsAppService(settings)
            plan_labels = {"featured": "⭐ Featured", "premium": "👑 Premium"}
            await whatsapp.send_text_message(
                sub["wa_id"],
                f"Your *{plan_labels.get(plan, plan)}* subscription is confirmed! 🎉\n"
                f"Your business is now live with premium features."
            )


async def _handle_subscription_deleted(subscription: dict, settings):
    """Handle subscription cancellation — downgrade to free."""
    from supabase import create_client

    stripe_sub_id = subscription.get("id")
    logger.info(f"Subscription deleted: {stripe_sub_id}")

    client = create_client(settings.SUPABASE_URL, settings.SUPABASE_KEY)

    result = (
        client.table("subscriptions")
        .select("*")
        .eq("stripe_subscription_id", stripe_sub_id)
        .limit(1)
        .execute()
    )

    if result.data:
        sub = result.data[0]

        # Downgrade to free
        client.table("subscriptions").update({
            "status": "canceled",
            "stripe_status": "canceled",
            "plan": "free",
        }).eq("stripe_subscription_id", stripe_sub_id).execute()

        # Remove featured badge
        if sub.get("business_id"):
            client.table("businesses").update({
                "is_featured": False
            }).eq("id", sub["business_id"]).execute()

        # Notify owner
        if sub.get("wa_id"):
            from app.services.whatsapp_service import WhatsAppService
            whatsapp = WhatsAppService(settings)
            await whatsapp.send_text_message(
                sub["wa_id"],
                "Your subscription has ended. Your listing is now on the *Free* plan.\n\n"
                "Want to re-activate? Just say *\"upgrade my business\"* anytime 🙏"
            )

        logger.info(f"Downgraded business {sub.get('business_id')} to free")


async def _handle_payment_failed(invoice: dict, settings):
    """Notify business owner that their payment failed."""
    stripe_sub_id = invoice.get("subscription")
    customer_email = invoice.get("customer_email", "")

    logger.warning(f"Payment failed: sub={stripe_sub_id}, email={customer_email}")

    if not stripe_sub_id:
        return

    from supabase import create_client
    client = create_client(settings.SUPABASE_URL, settings.SUPABASE_KEY)

    result = (
        client.table("subscriptions")
        .select("wa_id, business_id, businesses(name)")
        .eq("stripe_subscription_id", stripe_sub_id)
        .limit(1)
        .execute()
    )

    if result.data and result.data[0].get("wa_id"):
        sub = result.data[0]
        biz_name = sub.get("businesses", {}).get("name", "your business") if sub.get("businesses") else "your business"

        from app.services.whatsapp_service import WhatsAppService
        whatsapp = WhatsAppService(settings)
        await whatsapp.send_text_message(
            sub["wa_id"],
            f"⚠️ Payment failed for *{biz_name}*.\n\n"
            "Please update your payment method to keep your premium features.\n"
            "Your listing will stay active for now, but may be downgraded soon.\n\n"
            "Need help? Just reply here 🙏"
        )


# ── Helpers ──────────────────────────────────────────────────────

def _amount_to_plan(amount_cents: int) -> str | None:
    """Map Stripe amount (in cents) to our plan name."""
    if amount_cents == 1500:
        return "featured"
    elif amount_cents == 3000:
        return "premium"
    elif amount_cents == 0:
        return "free"
    else:
        logger.warning(f"Unknown amount: {amount_cents} cents")
        return None
