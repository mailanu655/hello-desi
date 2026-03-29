"""
Hello Desi — Scheduled Task Endpoints

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
