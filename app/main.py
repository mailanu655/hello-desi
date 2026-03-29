"""
Hello Desi — FastAPI Application Entry Point

Run with: uvicorn app.main:app --reload --port 8000
"""

import logging
import sys

from fastapi import FastAPI

from app.api.webhook import router as webhook_router
from app.api.tasks import router as tasks_router


def create_app() -> FastAPI:
    """Application factory for Hello Desi."""
    configure_logging()

    application = FastAPI(
        title="Hello Desi",
        description="AI-powered WhatsApp agent for the Indian diaspora in the USA",
        version="0.1.0",
    )

    # Register routes
    application.include_router(webhook_router, prefix="/api/v1")
    application.include_router(tasks_router, prefix="/api/v1")

    @application.get("/health")
    async def health_check():
        return {"status": "healthy", "service": "hello-desi"}

    return application


def configure_logging():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
        stream=sys.stdout,
    )


app = create_app()
