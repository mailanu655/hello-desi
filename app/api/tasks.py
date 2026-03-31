"""
Mira — Scheduled Task Endpoints

These endpoints are called by cron jobs (Render Cron, external scheduler,
or manual curl) to run periodic tasks.

Endpoints:
  POST /api/v1/tasks/proof-messages  — Send weekly proof messages to all business owners
  POST /api/v1/tasks/digest          — Send daily metro digest (coming soon)

Security:
  Protected by a simple bearer token (CRON_SECRET env var).
  Render cron jobs can include this in the request header.
"""

import logging
import os

from fastapi import APIRouter, Depends, HTTPException, Header
from config.settings import Settings, get_settings

router = APIRouter()
logger = logging.getLogger(__name__)


def verify_cron_secret(authorization: str | None = Header(None)):
    """
    Simple auth for cron endpoints.
    Accepts: Authorization: Bearer <CRON_SECRET>
    Falls back to allowing if CRON_SECRET is not set (dev mode).
    """
    cron_secret = os.environ.get("CRON_SECRET", "")
    if not cron_secret:
        # No secret configured — allow in dev mode
        logger.warning("CRON_SECRET not set — allowing unauthenticated cron request")
        return

    if not authorization:
        raise HTTPException(status_code=401, detail="Missing authorization header")

    token = authorization.replace("Bearer ", "").strip()
    if token != cron_secret:
        raise HTTPException(status_code=403, detail="Invalid cron secret")


@router.post("/tasks/proof-messages", dependencies=[Depends(verify_cron_secret)])
async def send_weekly_proof_messages(
    settings: Settings = Depends(get_settings),
):
    """
    Send weekly proof messages to all business owners.
    Called by cron every Monday at 10am EST.
    """
    from app.services.proof_message_service import send_proof_messages

    logger.info("Starting weekly proof message run...")
    summary = await send_proof_messages(settings)
    logger.info(f"Proof message run complete: {summary}")
    return {"status": "ok", "summary": summary}


@router.post("/tasks/digest", dependencies=[Depends(verify_cron_secret)])
async def send_daily_digest(
    settings: Settings = Depends(get_settings),
):
    """
    Send daily metro digest to opted-in users.
    Called by cron daily at 8am EST.
    """
    from app.services.digest_service import send_daily_digest

    logger.info("Starting daily digest run...")
    summary = await send_daily_digest(settings)
    logger.info(f"Digest run complete: {summary}")
    return {"status": "ok", "summary": summary}


@router.post("/tasks/evening-deals", dependencies=[Depends(verify_cron_secret)])
async def send_evening_deals(
    settings: Settings = Depends(get_settings),
):
    """
    Send evening "expiring deals" push to digest subscribers.
    Called by cron daily at 5pm EST. Only sends if deals are expiring.
    """
    from app.services.digest_service import send_evening_expiring_deals

    logger.info("Starting evening expiring deals push...")
    summary = await send_evening_expiring_deals(settings)
    logger.info(f"Evening push complete: {summary}")
    return {"status": "ok", "summary": summary}


@router.post("/tasks/expire-deals", dependencies=[Depends(verify_cron_secret)])
async def expire_deals(
    settings: Settings = Depends(get_settings),
):
    """
    Deactivate expired deals and notify business owners.
    Called by cron daily at 6am EST (before digest).
    """
    from app.services.deals_service import expire_stale_deals, cleanup_orphan_deals

    logger.info("Starting deal expiry run...")
    summary = await expire_stale_deals(settings)
    logger.info(f"Deal expiry run complete: {summary}")

    logger.info("Starting orphan deal cleanup...")
    orphan_summary = await cleanup_orphan_deals(settings)
    logger.info(f"Orphan cleanup complete: {orphan_summary}")

    return {"status": "ok", "summary": summary, "orphan_cleanup": orphan_summary}


@router.post("/tasks/replay-stripe-event/{event_id}", dependencies=[Depends(verify_cron_secret)])
async def replay_stripe_event(
    event_id: str,
    settings: Settings = Depends(get_settings),
):
    """
    Manually replay a dead-lettered or failed Stripe event.
    Fetches the event payload from stripe_events table and re-runs the handler.
    """
    from supabase import create_client

    client = create_client(settings.SUPABASE_URL, settings.SUPABASE_KEY)

    # Fetch the stored event
    result = (
        client.table("stripe_events")
        .select("*")
        .eq("id", event_id)
        .limit(1)
        .execute()
    )

    if not result.data:
        raise HTTPException(status_code=404, detail=f"Event {event_id} not found")

    stored = result.data[0]
    event_type = stored.get("event_type", "")
    raw_data = stored.get("raw_data") or {}
    payload = raw_data.get("payload")

    if not payload:
        raise HTTPException(
            status_code=400,
            detail=f"Event {event_id} has no stored payload to replay"
        )

    if stored.get("status") == "success":
        return {
            "status": "skipped",
            "message": f"Event {event_id} already processed successfully",
        }

    logger.info(f"Replaying Stripe event: {event_id} ({event_type})")

    # Import handlers from stripe_webhook
    from app.api.stripe_webhook import (
        _handle_checkout_completed,
        _handle_subscription_updated,
        _handle_subscription_deleted,
        _handle_payment_failed,
    )

    try:
        if event_type in ("checkout.session.completed", "checkout.session.async_payment_succeeded"):
            await _handle_checkout_completed(payload, event_id, settings)
        elif event_type == "customer.subscription.updated":
            await _handle_subscription_updated(payload, event_id, settings)
        elif event_type == "customer.subscription.deleted":
            await _handle_subscription_deleted(payload, event_id, settings)
        elif event_type == "invoice.payment_failed":
            await _handle_payment_failed(payload, event_id, settings)
        else:
            raise HTTPException(
                status_code=400,
                detail=f"Unsupported event type for replay: {event_type}"
            )

        # Mark as replayed successfully
        client.table("stripe_events").update({
            "status": "replayed",
        }).eq("id", event_id).execute()

        logger.info(f"Successfully replayed event {event_id}")
        return {"status": "ok", "message": f"Event {event_id} replayed successfully"}

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Replay failed for {event_id}: {e}")
        raise HTTPException(status_code=500, detail=f"Replay failed: {str(e)[:200]}")


@router.post("/tasks/nudge-inactive", dependencies=[Depends(verify_cron_secret)])
async def nudge_inactive_businesses(
    settings: Settings = Depends(get_settings),
):
    """
    Nudge business owners who haven't posted deals in 7+ days.
    Called by cron weekly on Wednesday at 11am EST.
    """
    from app.services.monetization_service import nudge_inactive_businesses

    logger.info("Starting inactive business nudge run...")
    summary = await nudge_inactive_businesses(settings)
    logger.info(f"Inactive nudge run complete: {summary}")
    return {"status": "ok", "summary": summary}


@router.get("/tasks/analytics", dependencies=[Depends(verify_cron_secret)])
async def get_analytics(
    settings: Settings = Depends(get_settings),
):
    """
    Quick analytics dashboard — call this to see how the platform is doing.
    Returns: total businesses, inquiries (today/week/all), subscriptions,
    digest subscribers, deals, and top searched businesses.
    """
    from datetime import datetime, timedelta, timezone
    from supabase import create_client

    client = create_client(settings.SUPABASE_URL, settings.SUPABASE_KEY)
    now = datetime.now(timezone.utc)
    today = now.replace(hour=0, minute=0, second=0, microsecond=0).isoformat()
    week_ago = (now - timedelta(days=7)).isoformat()

    # Counts
    biz = client.table("businesses").select("id", count="exact").execute()
    inq_all = client.table("inquiry_logs").select("id", count="exact").execute()
    inq_today = client.table("inquiry_logs").select("id", count="exact").gte("created_at", today).execute()
    inq_week = client.table("inquiry_logs").select("id", count="exact").gte("created_at", week_ago).execute()
    subs = client.table("subscriptions").select("id", count="exact").eq("status", "active").execute()
    digest = client.table("digest_subscribers").select("id", count="exact").eq("status", "active").execute()
    deals = client.table("deals").select("id", count="exact").execute()

    # Top 5 most-searched businesses this week
    top_biz_query = client.table("inquiry_logs").select("business_name").gte("created_at", week_ago).execute()
    biz_counts: dict[str, int] = {}
    for row in (top_biz_query.data or []):
        name = row.get("business_name", "Unknown")
        biz_counts[name] = biz_counts.get(name, 0) + 1
    top_5 = sorted(biz_counts.items(), key=lambda x: x[1], reverse=True)[:5]

    # User state + notification stats
    users = client.table("user_state").select("wa_id", count="exact").execute()
    notifs_sent = client.table("notification_log").select("id", count="exact").eq("status", "sent").execute()
    notifs_failed = client.table("notification_log").select("id", count="exact").eq("status", "failed").execute()

    # Boost funnel counters
    boost_attempted = client.table("notification_log").select("id", count="exact").eq("status", "conversion_event").ilike("details", "%boost_initiated%").execute()
    boost_paid = client.table("stripe_events").select("id", count="exact").eq("plan", "deal_boost").eq("status", "success").execute()
    boost_activated = client.table("notification_log").select("id", count="exact").eq("status", "conversion_event").ilike("details", "%deal_boosted%").execute()

    # Stripe dead letters (needs attention)
    dead_letters = client.table("stripe_events").select("id", count="exact").eq("status", "dead_letter").execute()

    return {
        "status": "ok",
        "timestamp": now.isoformat(),
        "businesses": biz.count or 0,
        "registered_users": users.count or 0,
        "inquiries": {
            "today": inq_today.count or 0,
            "this_week": inq_week.count or 0,
            "all_time": inq_all.count or 0,
        },
        "active_subscriptions": subs.count or 0,
        "digest_subscribers": digest.count or 0,
        "active_deals": deals.count or 0,
        "notifications": {
            "sent": notifs_sent.count or 0,
            "failed": notifs_failed.count or 0,
        },
        "top_businesses_this_week": [{"name": n, "inquiries": c} for n, c in top_5],
        "boost_funnel": {
            "attempted": boost_attempted.count or 0,
            "paid": boost_paid.count or 0,
            "activated": boost_activated.count or 0,
        },
        "stripe_dead_letters": dead_letters.count or 0,
    }
