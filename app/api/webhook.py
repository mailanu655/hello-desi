"""
Mira — WhatsApp Webhook Endpoints

Ported from Flask reference (python-whatsapp-bot-main/app/views.py) to FastAPI.

GET  /api/v1/webhook  — Meta verification (hub.challenge)
POST /api/v1/webhook  — Incoming message handler
"""

import logging

from fastapi import APIRouter, Depends, Query, Request, Response

from app.api.deps import verify_webhook_signature
from app.services.whatsapp_service import WhatsAppService
from app.services.user_state_service import is_first_time_user, check_rate_limit, get_user_context
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

        # ── Rate limiting (Supabase-backed) ─────────────────────
        if not check_rate_limit(wa_id, settings):
            whatsapp = WhatsAppService(settings)
            await whatsapp.send_text_message(
                wa_id,
                "You've reached today's limit 🙏\nCome back tomorrow or try again later!"
            )
            return {"status": "ok"}

        # ── First-time user onboarding (Supabase-backed) ────────
        if is_first_time_user(wa_id, name, settings):
            msg_lower = message_body.lower().strip()
            greetings = ["hi", "hello", "hey", "hola", "namaste", "start", "help"]
            if msg_lower in greetings or len(msg_lower) < 5:
                welcome = (
                    f"Hi {name}! I'm Mira 😊\n\n"
                    "I can help you find:\n"
                    "🛒 Indian groceries\n"
                    "🍛 Food & tiffins\n"
                    "👶 Babysitters\n"
                    "💸 Deals & services\n\n"
                    "Try asking:\n"
                    "👉 _\"Indian grocery near me\"_\n"
                    "👉 _\"babysitter in Columbus\"_\n"
                    "👉 _\"deals near me\"_\n\n"
                    "🏪 Own a business? Type *\"add my business\"* to get listed FREE!\n\n"
                    "📰 Want daily updates? Type *\"daily digest in [your city]\"*"
                )
                whatsapp = WhatsAppService(settings)
                await whatsapp.send_text_message(wa_id, welcome)
                return {"status": "ok"}

        # ── Returning user personalized greeting ────────────────
        msg_lower = message_body.lower().strip()
        greetings = {"hi", "hello", "hey", "hola", "namaste", "start", "help"}
        if msg_lower in greetings:
            user_ctx = get_user_context(wa_id, settings)
            if user_ctx:
                stored_name = user_ctx.get("name") or name
                welcome_back = (
                    f"Welcome back, {stored_name}! 👋\n\n"
                    "What can I help you with today?\n\n"
                    "🔍 Search for businesses or services\n"
                    "🏪 *\"add my business\"* — list your business\n"
                    "📊 *\"my stats\"* — see your business performance\n"
                    "💎 *\"upgrade\"* — boost your listing\n"
                    "📰 *\"daily digest in [city]\"* — get daily updates"
                )
                whatsapp = WhatsAppService(settings)
                await whatsapp.send_text_message(wa_id, welcome_back)
                return {"status": "ok"}

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
        elif deal_intent == "browse_today":
            deals = search_deals(message_body, settings, limit=5, today_only=True)
            response_text = format_deals_for_whatsapp(deals, query_type="today")
            whatsapp = WhatsAppService(settings)
            await whatsapp.send_text_message(wa_id, response_text)
            return {"status": "ok"}
        elif deal_intent == "browse":
            deals = search_deals(message_body, settings, limit=5)
            response_text = format_deals_for_whatsapp(deals)
            whatsapp = WhatsAppService(settings)
            await whatsapp.send_text_message(wa_id, response_text)
            return {"status": "ok"}

        # ── Check for monetization flow ─────────────────────────
        from app.services.monetization_service import (
            detect_monetization_intent,
            has_active_upgrade_session,
            handle_upgrade_message,
            start_upgrade_flow,
            get_business_stats,
            get_plan_status,
            get_notification_history,
        )

        # If user already has an active upgrade session, handle it
        if has_active_upgrade_session(wa_id):
            response_text = handle_upgrade_message(wa_id, message_body, settings)
            whatsapp = WhatsAppService(settings)
            await whatsapp.send_text_message(wa_id, response_text)
            return {"status": "ok"}

        # ── Check for digest subscription ─────────────────────────
        from app.services.digest_service import (
            detect_digest_intent,
            subscribe_to_digest,
            unsubscribe_from_digest,
        )

        digest_intent = detect_digest_intent(message_body)
        if digest_intent == "unsubscribe":
            response_text = unsubscribe_from_digest(wa_id, settings)
            whatsapp = WhatsAppService(settings)
            await whatsapp.send_text_message(wa_id, response_text)
            return {"status": "ok"}
        elif digest_intent == "subscribe":
            # Extract city from message or ask for it
            # Simple extraction: look for "in <city>" or "for <city>"
            msg_lower = message_body.lower()
            city = ""
            for prep in ["in ", "for "]:
                if prep in msg_lower:
                    parts = msg_lower.split(prep, 1)
                    if len(parts) > 1:
                        city = parts[1].strip().rstrip(".")
                        break
            if not city:
                # Try to find city from their business listing
                try:
                    from supabase import create_client
                    client = create_client(settings.SUPABASE_URL, settings.SUPABASE_KEY)
                    biz = (
                        client.table("businesses")
                        .select("city")
                        .ilike("source_id", f"%{wa_id}%")
                        .limit(1)
                        .execute()
                    )
                    if biz.data:
                        city = biz.data[0].get("city", "")
                except Exception:
                    pass

            if not city:
                response_text = (
                    "I'd love to set up your daily digest! 🌅\n\n"
                    "Which city are you in? Just say:\n"
                    "*daily digest in Dallas*\n"
                    "or\n"
                    "*daily digest in Houston*"
                )
            else:
                response_text = subscribe_to_digest(wa_id, city, settings)

            whatsapp = WhatsAppService(settings)
            await whatsapp.send_text_message(wa_id, response_text)
            return {"status": "ok"}

        # ── Check for weekly report request ────────────────────────
        report_phrases = ["my weekly report", "weekly report", "my report", "proof message"]
        if message_body.lower().strip() in report_phrases:
            from app.services.proof_message_service import send_proof_message_single
            result = await send_proof_message_single(wa_id, settings)
            if result:  # Only send if there's an error message
                whatsapp = WhatsAppService(settings)
                await whatsapp.send_text_message(wa_id, result)
            return {"status": "ok"}

        # Check if this is a new monetization intent
        money_intent = detect_monetization_intent(message_body)
        if money_intent == "upgrade":
            response_text = start_upgrade_flow(wa_id)
            whatsapp = WhatsAppService(settings)
            await whatsapp.send_text_message(wa_id, response_text)
            return {"status": "ok"}
        elif money_intent == "leads":
            response_text = get_notification_history(wa_id, settings)
            whatsapp = WhatsAppService(settings)
            await whatsapp.send_text_message(wa_id, response_text)
            return {"status": "ok"}
        elif money_intent == "stats":
            response_text = get_business_stats(wa_id, settings)
            whatsapp = WhatsAppService(settings)
            await whatsapp.send_text_message(wa_id, response_text)
            return {"status": "ok"}
        elif money_intent == "plan":
            response_text = get_plan_status(wa_id, settings)
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
