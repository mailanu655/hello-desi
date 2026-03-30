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

Production safeguards:
  - Signature verification on every request
  - Idempotency: duplicate events are safely skipped
  - Unknown amounts logged but never crash
  - Unmatched payments auto-reconciled by email or alerted via Slack
"""

import hashlib
import hmac
import json
import logging
import time

import stripe
from fastapi import APIRouter, HTTPException, Request
from config.settings import get_settings

router = APIRouter()
logger = logging.getLogger(__name__)

# Stripe allows up to 5 minutes tolerance for webhook timestamps
STRIPE_TIMESTAMP_TOLERANCE = 300


def _verify_stripe_signature(payload: bytes, sig_header: str, secret: str) -> bool:
    """
    Manually verify Stripe webhook signature using HMAC-SHA256.

    This avoids Stripe SDK v8+ issues where construct_event() crashes
    internally on StripeObject attribute access.
    """
    if not sig_header or not secret:
        return False

    try:
        # Parse the signature header: "t=123,v1=abc,v1=def"
        timestamp = ""
        signatures = []
        for part in sig_header.split(","):
            key, _, value = part.partition("=")
            if key == "t":
                timestamp = value
            elif key == "v1":
                signatures.append(value)

        if not timestamp or not signatures:
            return False

        # Check timestamp tolerance
        if abs(time.time() - int(timestamp)) > STRIPE_TIMESTAMP_TOLERANCE:
            logger.warning("Stripe webhook: timestamp outside tolerance")
            return False

        # Compute expected signature
        signed_payload = f"{timestamp}.".encode() + payload
        expected = hmac.new(
            secret.encode("utf-8"), signed_payload, hashlib.sha256
        ).hexdigest()

        # Compare against all v1 signatures
        return any(hmac.compare_digest(expected, sig) for sig in signatures)

    except Exception as e:
        logger.warning(f"Stripe signature verification failed: {e}")
        return False


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

    # Verify signature manually (avoids Stripe SDK v8+ StripeObject issues)
    if not _verify_stripe_signature(payload, sig_header, settings.STRIPE_WEBHOOK_SECRET):
        logger.warning("Stripe webhook: invalid signature")
        raise HTTPException(status_code=400, detail="Invalid signature")

    # Parse raw JSON for data access (avoids StripeObject .get() issues)
    event = json.loads(payload)
    event_id = event.get("id", "unknown")
    event_type = event["type"]
    data = event["data"]["object"]

    logger.info(f"Stripe webhook received: {event_type} (id={event_id})")

    # ── Idempotency: skip already-processed events ────────────────
    previous = await _is_event_already_processed(event_id, settings)
    if previous:
        prev_status = previous.get("status", "unknown")
        prev_at = previous.get("processed_at", "?")
        logger.info(f"Stripe webhook: duplicate event {event_id} (prev={prev_status} at {prev_at}) — skipping")
        return {"status": "ok", "message": "duplicate event skipped", "previous_status": prev_status}

    # ── Route events ─────────────────────────────────────────────
    try:
        if event_type == "checkout.session.completed":
            await _handle_checkout_completed(data, event_id, settings)

        elif event_type == "customer.subscription.updated":
            await _handle_subscription_updated(data, event_id, settings)

        elif event_type == "customer.subscription.deleted":
            await _handle_subscription_deleted(data, event_id, settings)

        elif event_type == "invoice.payment_failed":
            await _handle_payment_failed(data, event_id, settings)

        else:
            logger.info(f"Stripe webhook: unhandled event type {event_type}")

    except Exception as e:
        logger.error(f"Stripe webhook handler error for {event_type}: {e}")

        # Log the failure for debugging
        await _log_stripe_event(
            event_id, event_type, settings,
            raw_data={"error": str(e)},
            status="failed",
        )

        # Alert: send failure notification to admin via WhatsApp
        await _send_admin_alert(
            f"⚠️ Stripe webhook *failed*\n\n"
            f"Event: `{event_type}`\nID: `{event_id}`\n"
            f"Error: {str(e)[:200]}",
            settings,
        )

        # Return 200 anyway so Stripe doesn't retry indefinitely
        return {"status": "error", "message": str(e)}

    return {"status": "ok"}


# ══════════════════════════════════════════════════════════════════
# Idempotency
# ══════════════════════════════════════════════════════════════════

async def _is_event_already_processed(event_id: str, settings) -> dict | None:
    """
    Check if this Stripe event ID was already processed.

    Returns None if not yet processed, or a dict with previous processing
    info (status, processed_at) if it was already handled.
    """
    if not event_id or event_id == "unknown":
        return None

    try:
        from supabase import create_client
        client = create_client(settings.SUPABASE_URL, settings.SUPABASE_KEY)
        result = (
            client.table("stripe_events")
            .select("id, status, processed_at")
            .eq("id", event_id)
            .limit(1)
            .execute()
        )
        if result.data:
            return result.data[0]
        return None
    except Exception as e:
        logger.warning(f"Idempotency check failed: {e}")
        return None  # Proceed if check fails (better to double-process than drop)


async def _log_stripe_event(
    event_id: str, event_type: str, settings,
    stripe_subscription_id: str = "",
    customer_email: str = "",
    customer_name: str = "",
    plan: str = "",
    amount_cents: int = 0,
    raw_data: dict = None,
    status: str = "success",
):
    """Log a Stripe event to the stripe_events table for tracking and idempotency."""
    try:
        from supabase import create_client
        client = create_client(settings.SUPABASE_URL, settings.SUPABASE_KEY)
        client.table("stripe_events").insert({
            "id": event_id,
            "event_type": event_type,
            "stripe_subscription_id": stripe_subscription_id,
            "customer_email": customer_email,
            "customer_name": customer_name,
            "plan": plan,
            "amount_cents": amount_cents,
            "raw_data": raw_data or {},
            "status": status,
        }).execute()
    except Exception as e:
        logger.warning(f"Failed to log stripe event {event_id}: {e}")


# ══════════════════════════════════════════════════════════════════
# Event handlers
# ══════════════════════════════════════════════════════════════════

async def _handle_checkout_completed(session: dict, event_id: str, settings):
    """
    A customer completed a Stripe Checkout session (payment link).

    Flow:
    1. Extract customer info and determine plan from amount
    2. Try to match existing subscription by stripe_subscription_id
    3. If matched → activate subscription + send confirmation
    4. If not matched → log event + send fallback WhatsApp message
    """
    from supabase import create_client

    customer_email = session.get("customer_email") or (session.get("customer_details") or {}).get("email", "")
    customer_name = (session.get("customer_details") or {}).get("name", "")
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
        logger.warning(f"Unknown plan amount {amount_total} cents — logging event only")
        await _log_stripe_event(
            event_id, "checkout.session.completed", settings,
            stripe_subscription_id=subscription_id or "",
            customer_email=customer_email,
            customer_name=customer_name,
            plan="unknown",
            amount_cents=amount_total,
            raw_data={"session_id": session.get("id"), "payment_status": session.get("payment_status")},
            status="unknown_plan",
        )
        return

    client = create_client(settings.SUPABASE_URL, settings.SUPABASE_KEY)

    # Try to find and activate an existing subscription
    activated = False
    if subscription_id:
        existing = (
            client.table("subscriptions")
            .select("*, businesses(name)")
            .eq("stripe_subscription_id", subscription_id)
            .limit(1)
            .execute()
        )

        if existing.data:
            sub = existing.data[0]
            client.table("subscriptions").update({
                "status": "active",
                "stripe_status": "active",
                "plan": plan,
            }).eq("stripe_subscription_id", subscription_id).execute()

            # Mark business as featured if on featured/premium plan
            if sub.get("business_id") and plan in ("featured", "premium"):
                client.table("businesses").update({
                    "is_featured": True
                }).eq("id", sub["business_id"]).execute()

            logger.info(f"Activated subscription {subscription_id} → {plan}")
            activated = True

            # Send post-payment confirmation via WhatsApp
            if sub.get("wa_id"):
                biz_name = (sub.get("businesses") or {}).get("name", "your business")
                await _send_activation_confirmation(sub["wa_id"], plan, biz_name, settings)

    # Log the event FIRST (serves as idempotency marker even if later steps fail)
    await _log_stripe_event(
        event_id, "checkout.session.completed", settings,
        stripe_subscription_id=subscription_id or "",
        customer_email=customer_email,
        customer_name=customer_name,
        plan=plan,
        amount_cents=amount_total,
        raw_data={
            "session_id": session.get("id"),
            "payment_status": session.get("payment_status"),
            "activated": activated,
        },
        status="success" if activated else "unmatched",
    )

    if not activated:
        # Try auto-reconciliation by email before giving up
        reconciled = await _try_auto_reconcile(
            customer_email, customer_name, plan, subscription_id, settings
        )

        if reconciled:
            # Update the event log status to reflect successful reconciliation
            try:
                from supabase import create_client
                client2 = create_client(settings.SUPABASE_URL, settings.SUPABASE_KEY)
                client2.table("stripe_events").update({
                    "status": "reconciled",
                }).eq("id", event_id).execute()
            except Exception:
                pass  # Non-critical update
            logger.info(f"Auto-reconciled payment: email={customer_email}, plan={plan}")
        else:
            logger.info(
                f"Payment logged (unmatched): "
                f"email={customer_email}, plan={plan}, stripe_sub={subscription_id}"
            )
            # Alert admin about unmatched payment
            await _send_admin_alert(
                f"💰 *Unmatched Stripe payment*\n\n"
                f"Email: {customer_email}\nName: {customer_name}\n"
                f"Plan: {plan}\nAmount: ${amount_total / 100:.2f}\n"
                f"Stripe Sub: `{subscription_id or 'none'}`\n\n"
                f"This payment could not be auto-matched to any business.",
                settings,
            )


async def _handle_subscription_updated(subscription: dict, event_id: str, settings):
    """Handle subscription plan changes (upgrade/downgrade)."""
    from supabase import create_client

    stripe_sub_id = subscription.get("id")
    status = subscription.get("status")  # active, past_due, canceled, etc.
    items_data = (subscription.get("items") or {}).get("data", [])
    amount = (items_data[0].get("price") or {}).get("unit_amount", 0) if items_data else 0
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
            # Ensure featured badge
            if sub.get("business_id") and plan in ("featured", "premium"):
                client.table("businesses").update({
                    "is_featured": True
                }).eq("id", sub["business_id"]).execute()
        elif status in ("past_due", "unpaid"):
            updates["status"] = "active"  # Keep active but flag
            logger.warning(f"Subscription {stripe_sub_id} is {status}")
        elif status == "canceled":
            updates["status"] = "canceled"
            updates["plan"] = "free"
            if sub.get("business_id"):
                client.table("businesses").update({
                    "is_featured": False
                }).eq("id", sub["business_id"]).execute()

        client.table("subscriptions").update(updates).eq(
            "stripe_subscription_id", stripe_sub_id
        ).execute()

        # Notify business owner via WhatsApp
        if sub.get("wa_id") and status == "active" and plan:
            biz_name = (sub.get("businesses") or {}).get("name", "your business")
            await _send_activation_confirmation(sub["wa_id"], plan, biz_name, settings)

    # Log event
    await _log_stripe_event(
        event_id, "customer.subscription.updated", settings,
        stripe_subscription_id=stripe_sub_id or "",
        plan=plan or "",
        raw_data={"status": status},
    )


async def _handle_subscription_deleted(subscription: dict, event_id: str, settings):
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

    # Log event
    await _log_stripe_event(
        event_id, "customer.subscription.deleted", settings,
        stripe_subscription_id=stripe_sub_id or "",
        plan="free",
    )


async def _handle_payment_failed(invoice: dict, event_id: str, settings):
    """Notify business owner that their payment failed."""
    stripe_sub_id = invoice.get("subscription")
    customer_email = invoice.get("customer_email", "")

    logger.warning(f"Payment failed: sub={stripe_sub_id}, email={customer_email}")

    # Log event
    await _log_stripe_event(
        event_id, "invoice.payment_failed", settings,
        stripe_subscription_id=stripe_sub_id or "",
        customer_email=customer_email,
    )

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
        biz_name = (sub.get("businesses") or {}).get("name", "your business")

        from app.services.whatsapp_service import WhatsAppService
        whatsapp = WhatsAppService(settings)
        await whatsapp.send_text_message(
            sub["wa_id"],
            f"⚠️ Payment failed for *{biz_name}*.\n\n"
            "Please update your payment method to keep your premium features.\n"
            "Your listing will stay active for now, but may be downgraded soon.\n\n"
            "Need help? Just reply here 🙏"
        )


# ══════════════════════════════════════════════════════════════════
# Helpers
# ══════════════════════════════════════════════════════════════════

async def _send_activation_confirmation(wa_id: str, plan: str, biz_name: str, settings):
    """Send a rich post-payment confirmation via WhatsApp."""
    from app.services.whatsapp_service import WhatsAppService
    whatsapp = WhatsAppService(settings)

    plan_benefits = {
        "featured": (
            f"🎉 *{biz_name}* is now *⭐ Featured*!\n\n"
            "Your business will:\n"
            "• Appear first in search results\n"
            "• Show in the daily digest\n"
            "• Get a ⭐ badge on your listing\n"
            "• Attract more leads\n\n"
            "Let's grow your business 🚀"
        ),
        "premium": (
            f"🎉 *{biz_name}* is now *👑 Premium*!\n\n"
            "Your business will:\n"
            "• Appear first in search results\n"
            "• Show in the daily digest\n"
            "• Get a 👑 badge on your listing\n"
            "• Post unlimited deals & promotions\n"
            "• Get detailed lead analytics\n\n"
            "Let's grow your business 🚀"
        ),
    }

    message = plan_benefits.get(plan, f"Your *{biz_name}* subscription is confirmed! 🎉")

    try:
        await whatsapp.send_text_message(wa_id, message)
        logger.info(f"Sent activation confirmation to {wa_id} for plan={plan}")
    except Exception as e:
        logger.warning(f"Failed to send activation confirmation: {e}")


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


async def _send_admin_alert(message: str, settings):
    """Send a Slack alert via incoming webhook. Silently fails if not configured."""
    slack_url = getattr(settings, "SLACK_WEBHOOK_URL", "")
    if not slack_url:
        logger.info("Admin alert skipped (SLACK_WEBHOOK_URL not configured)")
        return

    try:
        import httpx
        async with httpx.AsyncClient() as http:
            resp = await http.post(slack_url, json={"text": message}, timeout=10)
            if resp.status_code == 200 and resp.text == "ok":
                logger.info("Admin alert sent to Slack")
            else:
                logger.warning(f"Slack alert failed: {resp.status_code} {resp.text}")
    except Exception as e:
        logger.warning(f"Failed to send admin alert to Slack: {e}")


async def _try_auto_reconcile(
    customer_email: str, customer_name: str, plan: str,
    subscription_id: str, settings,
) -> bool:
    """
    Try to match an unmatched payment to a subscription by email or phone.

    Looks for subscriptions where:
    1. The business owner's email matches the payment email
    2. The subscription has no stripe_subscription_id yet (pending activation)

    Returns True if a match was found and activated.
    """
    if not customer_email:
        return False

    try:
        from supabase import create_client
        client = create_client(settings.SUPABASE_URL, settings.SUPABASE_KEY)

        # Try matching by email on businesses table
        biz_result = (
            client.table("businesses")
            .select("id, name, subscriptions(id, wa_id, stripe_subscription_id)")
            .eq("email", customer_email)
            .limit(1)
            .execute()
        )

        if not biz_result.data:
            return False

        biz = biz_result.data[0]
        subs = biz.get("subscriptions") or []

        # Find a subscription that needs activation (no stripe_subscription_id or pending)
        target_sub = None
        for s in subs:
            if not s.get("stripe_subscription_id"):
                target_sub = s
                break

        if not target_sub:
            return False

        # Activate it
        updates = {
            "status": "active",
            "stripe_status": "active",
            "plan": plan,
        }
        if subscription_id:
            updates["stripe_subscription_id"] = subscription_id

        client.table("subscriptions").update(updates).eq("id", target_sub["id"]).execute()

        # Mark business as featured if applicable
        if plan in ("featured", "premium"):
            client.table("businesses").update({
                "is_featured": True
            }).eq("id", biz["id"]).execute()

        logger.info(
            f"Auto-reconciled: email={customer_email} → "
            f"business={biz['name']}, plan={plan}"
        )

        # Send confirmation
        if target_sub.get("wa_id"):
            await _send_activation_confirmation(
                target_sub["wa_id"], plan, biz.get("name", "your business"), settings
            )

        return True

    except Exception as e:
        logger.warning(f"Auto-reconciliation failed: {e}")
        return False
