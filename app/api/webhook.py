"""
Hello Desi — WhatsApp Webhook Endpoints

Ported from Flask reference (python-whatsapp-bot-main/app/views.py) to FastAPI.

GET  /api/v1/webhook  — Meta verification (hub.challenge)
POST /api/v1/webhook  — Incoming message handler
"""

import logging

from fastapi import APIRouter, Depends, Query, Request, Response

from app.api.deps import verify_webhook_signature
from app.services.whatsapp_service import WhatsAppService
from app.utils.whatsapp_utils import is_valid_whatsapp_message, extract_message_data
from config.settings import Settings, get_settings

router = APIRouter()
logger = logging.getLogger(__name__)


@router.get("/webhook")
async def verify_webhook(
    settings: Settings = Depends(get_settings),
    hub_mode: str | None = Query(None, alias="hub.mode"),
    hub_verify_token: str | None = Query(None, alias="hub.verify_token"),
    hub_challenge: str | None = Query(None, alias="hub.challenge"),
):
    """
    Webhook verification endpoint for Meta.

    When you configure a webhook in the Meta Developer Dashboard,
    Meta sends a GET request with hub.mode, hub.verify_token, and hub.challenge.
    We must return the challenge value if the token matches.
    """
    if hub_mode and hub_verify_token:
        if hub_mode == "subscribe" and hub_verify_token == settings.VERIFY_TOKEN:
            logger.info("WEBHOOK_VERIFIED")
            return Response(content=hub_challenge, media_type="text/plain")
        else:
            logger.warning("VERIFICATION_FAILED")
            return Response(
                content='{"status": "error", "message": "Verification failed"}',
                status_code=403,
                media_type="application/json",
            )

    logger.warning("MISSING_PARAMETER")
    return Response(
        content='{"status": "error", "message": "Missing parameters"}',
        status_code=400,
        media_type="application/json",
    )


@router.post("/webhook", dependencies=[Depends(verify_webhook_signature)])
async def handle_message(
    request: Request,
    settings: Settings = Depends(get_settings),
):
    """
    Handle incoming WhatsApp messages.

    Every message triggers 4 webhooks: message, sent, delivered, read.
    We only process actual messages, not status updates.
    """
    body = await request.json()

    # Check if it's a status update (sent, delivered, read) — ignore
    if (
        body.get("entry", [{}])[0]
        .get("changes", [{}])[0]
        .get("value", {})
        .get("statuses")
    ):
        logger.info("Received WhatsApp status update — ignoring.")
        return {"status": "ok"}

    # Process actual messages
    if is_valid_whatsapp_message(body):
        message_data = extract_message_data(body)
        wa_id = message_data["wa_id"]
        name = message_data["name"]
        message_body = message_data["message_body"]

        logger.info(f"Message from {name} ({wa_id}): {message_body[:50]}...")

        # ── Check for business registration / update flow ────────
        from app.services.business_registration import (
            detect_registration_intent,
            has_active_session,
            handle_registration_message,
            start_add_flow,
            start_update_flow,
        )

        # If user already has an active registration session, handle it
        if has_active_session(wa_id):
            response_text = handle_registration_message(wa_id, message_body, settings)
            whatsapp = WhatsAppService(settings)
            await whatsapp.send_text_message(wa_id, response_text)
            return {"status": "ok"}

        # Check if this is a new registration intent
        reg_intent = detect_registration_intent(message_body)
        if reg_intent == "add":
            response_text = start_add_flow(wa_id)
            whatsapp = WhatsAppService(settings)
            await whatsapp.send_text_message(wa_id, response_text)
            return {"status": "ok"}
        elif reg_intent == "update":
            response_text = start_update_flow(wa_id)
            whatsapp = WhatsAppService(settings)
            await whatsapp.send_text_message(wa_id, response_text)
            return {"status": "ok"}

        # ── Check for deals flow ─────────────────────────────────
        from app.services.deals_service import (
            detect_deal_intent,
            has_active_deal_session,
            handle_deal_message,
            start_deal_flow,
            search_deals,
            format_deals_for_whatsapp,
        )

        # If user already has an active deal-posting session, handle it
        if has_active_deal_session(wa_id):
            response_text = handle_deal_message(wa_id, message_body, settings)
            whatsapp = WhatsAppService(settings)
            await whatsapp.send_text_message(wa_id, response_text)
            return {"status": "ok"}

        # Check if this is a new deal intent
        deal_intent = detect_deal_intent(message_body)
        if deal_intent == "post":
            response_text = start_deal_flow(wa_id)
            whatsapp = WhatsAppService(settings)
            await whatsapp.send_text_message(wa_id, response_text)
            return {"status": "ok"}
        elif deal_intent == "browse":
            deals = search_deals(message_body, settings, limit=5)
            response_text = format_deals_for_whatsapp(deals)
            whatsapp = WhatsAppService(settings)
            await whatsapp.send_text_message(wa_id, response_text)
            return {"status": "ok"}

        # ── Normal AI response flow ──────────────────────────────
        from app.services.claude_service import generate_response

        response_text = await generate_response(
            message=message_body,
            wa_id=wa_id,
            name=name,
            settings=settings,
        )

        # Send reply back to the SENDER (not a hardcoded recipient)
        whatsapp = WhatsAppService(settings)
        await whatsapp.send_text_message(wa_id, response_text)

        return {"status": "ok"}

    return Response(
        content='{"status": "error", "message": "Not a valid WhatsApp message"}',
        status_code=404,
        media_type="application/json",
    )
