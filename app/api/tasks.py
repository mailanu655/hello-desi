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

    # New: user state + notification stats
    users = client.table("user_state").select("wa_id", count="exact").execute()
    notifs_sent = client.table("notification_log").select("id", count="exact").eq("status", "sent").execute()
    notifs_failed = client.table("notification_log").select("id", count="exact").eq("status", "failed").execute()

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
    }
