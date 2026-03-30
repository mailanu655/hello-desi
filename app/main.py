"""
Mira — FastAPI Application Entry Point

Run with: uvicorn app.main:app --reload --port 8000
"""

import asyncio
import logging
import sys

from contextlib import asynccontextmanager

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from fastapi import FastAPI

from app.api.webhook import router as webhook_router
from app.api.tasks import router as tasks_router
from app.api.stripe_webhook import router as stripe_router

logger = logging.getLogger(__name__)

# ── Scheduled job wrappers ─────────────────────────────────────────
async def _run_daily_digest():
    """Send daily metro digest — runs every day at 8:00 AM EST."""
    try:
        from config.settings import get_settings
        from app.services.digest_service import send_daily_digest

        settings = get_settings()
        logger.info("⏰ CRON: Starting daily digest run...")
        summary = await send_daily_digest(settings)
        logger.info(f"⏰ CRON: Digest complete — {summary}")
    except Exception as e:
        logger.error(f"⏰ CRON: Digest failed — {e}")


async def _run_weekly_proof_messages():
    """Send weekly proof messages — runs every Monday at 10:00 AM EST."""
    try:
        from config.settings import get_settings
        from app.services.proof_message_service import send_proof_messages

        settings = get_settings()
        logger.info("⏰ CRON: Starting weekly proof message run...")
        summary = await send_proof_messages(settings)
        logger.info(f"⏰ CRON: Proof messages complete — {summary}")
    except Exception as e:
        logger.error(f"⏰ CRON: Proof messages failed — {e}")


# ── Lifespan (startup/shutdown) ────────────────────────────────────
@asynccontextmanager
async def lifespan(application: FastAPI):
    """Start scheduler on startup, shut down on exit."""
    scheduler = AsyncIOScheduler()

    # Daily digest — 8:00 AM US/Eastern every day
    scheduler.add_job(
        _run_daily_digest,
        CronTrigger(hour=8, minute=0, timezone="America/New_York"),
        id="daily_digest",
        name="Daily Metro Digest",
        replace_existing=True,
    )

    # Weekly proof messages — Monday 10:00 AM US/Eastern
    scheduler.add_job(
        _run_weekly_proof_messages,
        CronTrigger(day_of_week="mon", hour=10, minute=0, timezone="America/New_York"),
        id="weekly_proof_messages",
        name="Weekly Proof Messages",
        replace_existing=True,
    )

    scheduler.start()
    logger.info("⏰ APScheduler started — digest daily 8am ET, proof msgs Monday 10am ET")
    yield
    scheduler.shutdown()
    logger.info("⏰ APScheduler shut down")


def create_app() -> FastAPI:
    """Application factory for Mira."""
    configure_logging()

    application = FastAPI(
        title="Mira",
        description="AI-powered WhatsApp agent for the Indian diaspora in the USA",
        version="0.1.0",
        lifespan=lifespan,
    )

    # Register routes
    application.include_router(webhook_router, prefix="/api/v1")
    application.include_router(tasks_router, prefix="/api/v1")
    application.include_router(stripe_router, prefix="/api/v1")

    @application.get("/health")
    async def health_check():
        return {"status": "healthy", "service": "mira"}

    return application


def configure_logging():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
        stream=sys.stdout,
    )


app = create_app()
