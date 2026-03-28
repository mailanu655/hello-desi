"""
Hello Desi — Shared FastAPI Dependencies

Includes webhook signature verification (ported from Flask decorator pattern).
"""

import hashlib
import hmac
import logging

from fastapi import Depends, HTTPException, Request

from config.settings import Settings, get_settings

logger = logging.getLogger(__name__)


async def verify_webhook_signature(
    request: Request,
    settings: Settings = Depends(get_settings),
):
    """
    FastAPI dependency that validates the incoming webhook payload signature.

    Ported from: python-whatsapp-bot-main/app/decorators/security.py

    Meta signs every webhook POST with HMAC SHA256 using your App Secret.
    The signature is in the X-Hub-Signature-256 header as "sha256=<hex>".
    """
    signature_header = request.headers.get("X-Hub-Signature-256", "")

    if not signature_header.startswith("sha256="):
        logger.warning("Missing or malformed X-Hub-Signature-256 header")
        raise HTTPException(status_code=403, detail="Invalid signature")

    received_signature = signature_header[7:]  # Strip "sha256=" prefix

    # Read the raw body for signature verification
    body_bytes = await request.body()
    payload = body_bytes.decode("utf-8")

    # Compute expected signature
    expected_signature = hmac.new(
        key=settings.APP_SECRET.encode("latin-1"),
        msg=payload.encode("utf-8"),
        digestmod=hashlib.sha256,
    ).hexdigest()

    if not hmac.compare_digest(expected_signature, received_signature):
        logger.warning("Webhook signature verification FAILED")
        raise HTTPException(status_code=403, detail="Invalid signature")

    logger.debug("Webhook signature verified successfully")
