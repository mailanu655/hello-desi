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
    # Critical events (payment/activation) return 500 on failure so Stripe retries.
    # Non-critical events (notifications) return 200 even on failure.
    CRITICAL_EVENTS = {
        "checkout.session.completed",
        "checkout.session.async_payment_succeeded",
        "customer.subscription.updated",
        "customer.subscription.deleted",
    }

    try:
        if event_type in ("checkout.session.completed", "checkout.session.async_payment_succeeded"):
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

        # Dead-letter: store failed event for manual replay
        await _store_dead_letter(event_id, event_type, data, str(e), settings)

        # Alert admin
        await _send_admin_alert(
            f"⚠️ Stripe webhook *failed*\n\n"
            f"Event: `{event_type}`\nID: `{event_id}`\n"
            f"Error: {str(e)[:200]}",
            settings,
        )

        # Critical events: return 500 so Stripe retries (up to ~3 days)
        if event_type in CRITICAL_EVENTS:
            raise HTTPException(status_code=500, detail=f"Handler failed: {str(e)[:100]}")

        # Non-critical: return 200 to prevent infinite retry
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


async def _store_dead_letter(
    event_id: str, event_type: str, data: dict,
    error: str, settings,
):
    """Store failed event for manual replay / debugging."""
    try:
        from supabase import create_client
        client = create_client(settings.SUPABASE_URL, settings.SUPABASE_KEY)
        client.table("stripe_events").upsert({
            "id": event_id,
            "event_type": event_type,
            "raw_data": {"payload": data, "error": error},
            "status": "dead_letter",
        }).execute()
        logger.info(f"Dead-lettered event {event_id}")
    except Exception as e:
        logger.error(f"Failed to dead-letter event {event_id}: {e}")


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

    # Determine which plan (price_id → metadata → amount fallback)
    plan = _resolve_plan(session)

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

    # ── Deal Boost (one-time $4.99) ─────────────────────────────
    if plan == "deal_boost":
        await _handle_deal_boost_payment(session, event_id, customer_email, settings)
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

    # Resolve plan: prefer price_id mapping, fall back to amount
    plan = None
    if items_data:
        price_id = (items_data[0].get("price") or {}).get("id", "")
        plan = PRICE_TO_PLAN.get(price_id)
        if not plan:
            amount = (items_data[0].get("price") or {}).get("unit_amount", 0)
            plan = _amount_to_plan_fallback(amount)

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
# Deal Boost handler
# ══════════════════════════════════════════════════════════════════

async def _handle_deal_boost_payment(session: dict, event_id: str, customer_email: str, settings):
    """
    Handle a $4.99 deal boost payment.

    Strategy to find the buyer's wa_id:
    1. Check checkout session metadata for wa_id (if we set it)
    2. Match by customer_email → businesses → wa_id
    3. Check Redis pending_boost keys for recent requests

    Once wa_id is found, call activate_boost_for_deal() to mark the deal.
    """
    from supabase import create_client
    from app.services.deals_service import activate_boost_for_deal

    wa_id = None

    # Strategy 0: client_reference_id (set on payment link URL — most reliable)
    client_ref = session.get("client_reference_id")
    if client_ref:
        wa_id = client_ref
        logger.info(f"Deal boost: found wa_id from client_reference_id: {wa_id}")

    # Strategy 1: metadata (future-proof — for Checkout Sessions with metadata)
    if not wa_id:
        metadata = session.get("metadata") or {}
        if metadata.get("wa_id"):
            wa_id = metadata["wa_id"]
            logger.info(f"Deal boost: found wa_id from metadata: {wa_id}")

    # Strategy 2: match by email → business → wa_id
    if not wa_id and customer_email:
        try:
            client = create_client(settings.SUPABASE_URL, settings.SUPABASE_KEY)
            biz_result = (
                client.table("businesses")
                .select("source_id")
                .eq("email", customer_email)
                .limit(1)
                .execute()
            )
            if biz_result.data:
                source_id = biz_result.data[0].get("source_id", "")
                # source_id format: "user_{wa_id}_..."
                if source_id.startswith("user_"):
                    parts = source_id.split("_", 2)
                    if len(parts) >= 2:
                        wa_id = parts[1]
                        logger.info(f"Deal boost: matched wa_id from email: {wa_id}")
        except Exception as e:
            logger.warning(f"Deal boost email lookup failed: {e}")

    # Strategy 3: scan Redis for recent pending_boost keys
    if not wa_id:
        try:
            from app.services.session_store import _get_redis
            r = _get_redis(settings)
            if r:
                keys = r.keys("pending_boost:*")
                if keys and len(keys) == 1:
                    # Only one pending boost — safe to assume it's this payment
                    wa_id = keys[0].split(":", 1)[1] if isinstance(keys[0], str) else keys[0].decode().split(":", 1)[1]
                    logger.info(f"Deal boost: matched wa_id from single pending_boost key: {wa_id}")
                elif keys and len(keys) > 1:
                    logger.warning(f"Deal boost: {len(keys)} pending_boost keys — can't auto-match")
        except Exception as e:
            logger.warning(f"Deal boost Redis scan failed: {e}")

    if not wa_id:
        logger.warning(
            f"Deal boost payment received but can't identify buyer. "
            f"email={customer_email}, session={session.get('id')}"
        )
        await _log_stripe_event(
            event_id, "checkout.session.completed", settings,
            customer_email=customer_email,
            plan="deal_boost",
            amount_cents=499,
            raw_data={"session_id": session.get("id"), "status": "unmatched_boost"},
            status="unmatched",
        )
        await _send_admin_alert(
            f"💰 *Unmatched deal boost payment*\n\n"
            f"Email: {customer_email}\n"
            f"Session: `{session.get('id')}`\n\n"
            f"Could not identify the buyer's WhatsApp ID.",
            settings,
        )
        return

    # Activate the boost
    success = activate_boost_for_deal(wa_id, settings)

    await _log_stripe_event(
        event_id, "checkout.session.completed", settings,
        customer_email=customer_email,
        plan="deal_boost",
        amount_cents=499,
        raw_data={
            "session_id": session.get("id"),
            "wa_id": wa_id,
            "activated": success,
        },
        status="success" if success else "activation_failed",
    )

    # Send confirmation via WhatsApp
    if success:
        from app.services.whatsapp_service import WhatsAppService
        whatsapp = WhatsAppService(settings)
        await whatsapp.send_text_message(
            wa_id,
            "🚀 *Boost activated!*\n\n"
            "Your deal is now at the *top of search results* for 24 hours.\n"
            "⏰ Boost expires tomorrow at this time.\n\n"
            "Thanks for supporting Hello Desi! 🙏"
        )
        logger.info(f"Deal boost activated and confirmed for {wa_id}")
    else:
        logger.error(f"Deal boost activation failed for {wa_id} after payment")
        await _send_admin_alert(
            f"⚠️ *Deal boost activation failed after payment*\n\n"
            f"wa_id: {wa_id}\nEmail: {customer_email}\n"
            f"Payment was successful but boost could not be applied.",
            settings,
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


# ── Price ID → Plan mapping (immune to price changes / discounts / tax) ──
PRICE_TO_PLAN = {
    "price_1TGTKcCytUHyGW3SBY7MwnUS": "featured",    # $15/mo
    "price_1TGTLyCytUHyGW3SKghaqije": "premium",      # $30/mo
    "price_1TGpqbCytUHyGW3SY56Trvau": "deal_boost",   # $4.99 one-time
}


def _resolve_plan(session: dict) -> str | None:
    """
    Determine plan from checkout session.

    Strategy (ordered by reliability):
    1. price_id from line_items (most reliable — immune to discounts/tax)
    2. metadata.plan (explicit, if set)
    3. amount fallback (legacy safety net)
    """
    # Strategy 1: price_id from line_items
    line_items = session.get("line_items", {})
    items_data = line_items.get("data", []) if isinstance(line_items, dict) else []
    for item in items_data:
        price_obj = item.get("price") or {}
        price_id = price_obj.get("id", "")
        if price_id in PRICE_TO_PLAN:
            return PRICE_TO_PLAN[price_id]

    # Strategy 2: metadata
    metadata = session.get("metadata") or {}
    if metadata.get("plan"):
        return metadata["plan"]

    # Strategy 3: amount fallback (handles legacy links / edge cases)
    amount = session.get("amount_total", 0)
    return _amount_to_plan_fallback(amount)


def _amount_to_plan_fallback(amount_cents: int) -> str | None:
    """Legacy fallback: map amount to plan. Use _resolve_plan() instead."""
    if amount_cents == 499:
        return "deal_boost"
    elif amount_cents == 1500:
        return "featured"
    elif amount_cents == 3000:
        return "premium"
    elif amount_cents == 0:
        return "free"
    else:
        logger.warning(f"Unknown plan amount: {amount_cents} cents")
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
