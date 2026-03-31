"""
Mira — WhatsApp Webhook Endpoints

Ported from Flask reference (python-whatsapp-bot-main/app/views.py) to FastAPI.

GET  /api/v1/webhook  — Meta verification (hub.challenge)
POST /api/v1/webhook  — Incoming message handler

Production hardening (v2):
- request_id correlation on every log line
- Message deduplication via Redis (prevents double-processing on webhook retry)
- Per-user processing lock (prevents multi-message race conditions)
- Non-text message handling (images, audio, etc. → graceful response)
- Session check before greeting (prevents mid-flow interruption)
- Global cancel/stop command works across all flows
- Empty message handling
- Atomic Redis rate limiting (replaces Supabase read-then-write)
- Background message sending (webhook responds immediately)
- Retry logic in WhatsAppService (3 attempts with exponential backoff)
- Explicit Claude API timeouts (8s Haiku, 15s Sonnet)
"""

import logging
import uuid

from fastapi import APIRouter, BackgroundTasks, Depends, Query, Request, Response

from app.api.deps import verify_webhook_signature
from app.services.session_store import (
    message_seen,
    acquire_user_lock,
    release_user_lock,
    check_rate_limit_atomic,
    delete_session,
)
from app.services.whatsapp_service import WhatsAppService
from app.services.user_state_service import is_first_time_user, check_rate_limit, get_user_context
from app.utils.whatsapp_utils import is_valid_whatsapp_message, extract_message_data
from config.settings import Settings, get_settings

router = APIRouter()
logger = logging.getLogger(__name__)

# Daily message limit (matches user_state_service.DAILY_MESSAGE_LIMIT)
DAILY_MESSAGE_LIMIT = 50


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
    background_tasks: BackgroundTasks,
    settings: Settings = Depends(get_settings),
):
    """
    Handle incoming WhatsApp messages.

    Every message triggers 4 webhooks: message, sent, delivered, read.
    We only process actual messages, not status updates.

    Pipeline order (intentional):
      1. Status filter (ignore delivery receipts)
      2. Valid message check
      3. Deduplication (Redis — prevent double-processing on webhook retry)
      4. Return 200 immediately → process in background task
         4a. Per-user lock (prevents multi-message race conditions)
         4b. Non-text guard (images/audio/etc. → graceful response)
         4c. Empty message guard
         4d. Global cancel/stop command
         4e. Rate limiting (atomic Redis INCR + Supabase fallback)
         4f. First-time user onboarding
         4g. Active session check (BEFORE greeting)
         4h. Returning user greeting
         4i. Intent routing (registration → deals → monetization → digest → report)
         4j. Claude AI fallback
    """
    # ── Generate request ID for log correlation ─────────────────
    request_id = str(uuid.uuid4())[:8]

    body = await request.json()

    # ── 1. Status update filter ─────────────────────────────────
    if (
        body.get("entry", [{}])[0]
        .get("changes", [{}])[0]
        .get("value", {})
        .get("statuses")
    ):
        return {"status": "ok"}

    # ── 2. Valid message check ──────────────────────────────────
    if not is_valid_whatsapp_message(body):
        return Response(
            content='{"status": "error", "message": "Not a valid WhatsApp message"}',
            status_code=404,
            media_type="application/json",
        )

    message_data = extract_message_data(body)
    wa_id = message_data["wa_id"]
    name = message_data["name"]
    message_body = message_data["message_body"]
    message_type = message_data["message_type"]
    message_id = message_data["message_id"]

    logger.info(
        f"[{request_id}] Message from {name} ({wa_id}): "
        f"type={message_type} body={message_body[:50]}..."
    )

    # ── 3. Deduplication (Redis) ────────────────────────────────
    if message_seen(message_id, settings):
        logger.info(f"[{request_id}] Duplicate message {message_id} — skipping")
        return {"status": "ok"}

    # ── 4. Return 200 immediately, process in background ────────
    background_tasks.add_task(
        _process_message, wa_id, name, message_body, message_type, request_id, settings
    )
    return {"status": "ok"}


async def _process_message(
    wa_id: str,
    name: str,
    message_body: str,
    message_type: str,
    request_id: str,
    settings: Settings,
) -> None:
    """
    Process a WhatsApp message in the background.

    Separated from the webhook handler so we can return 200 to Meta immediately
    and avoid timeout issues on slow Claude responses.
    """
    # Helper: create WhatsAppService with request_id for correlated logs
    def _wa() -> WhatsAppService:
        return WhatsAppService(settings, request_id=request_id)

    # ── 4a. Per-user lock (prevent multi-message race) ──────────
    if not acquire_user_lock(wa_id, settings):
        logger.info(f"[{request_id}] Lock held for {wa_id} — skipping concurrent message")
        return

    try:
        # ── 4b. Non-text message guard ──────────────────────────
        if message_type != "text" and not message_body:
            logger.info(f"[{request_id}] Non-text message ({message_type}) — sending helper")
            await _wa().send_text_message(
                wa_id,
                "I can only understand text messages for now 😊\n\n"
                "Try typing your request!"
            )
            return

        # ── 4c. Empty message guard ─────────────────────────────
        if not message_body.strip():
            logger.info(f"[{request_id}] Empty message from {wa_id}")
            await _wa().send_text_message(
                wa_id,
                "I didn't catch that 😅\n\nTry typing something like:\n"
                "👉 _\"Indian grocery near me\"_\n"
                "👉 _\"deals in Columbus\"_"
            )
            return

        # ── 4d. Global cancel/stop command ──────────────────────
        msg_lower = message_body.lower().strip()
        if msg_lower in {"cancel", "stop", "quit", "exit", "nevermind", "never mind"}:
            # Clear ALL possible active sessions for this user
            from app.services.business_registration import has_active_session as has_reg
            from app.services.deals_service import has_active_deal_session as has_deal
            from app.services.monetization_service import has_active_upgrade_session as has_upgrade

            had_session = False
            for prefix in ["reg:", "deal:", "upgrade:"]:
                key = f"{prefix}{wa_id}"
                try:
                    delete_session(key, settings)
                    had_session = True
                except Exception:
                    pass

            if had_session:
                logger.info(f"[{request_id}] Global cancel — cleared sessions for {wa_id}")
            await _wa().send_text_message(
                wa_id,
                "Cancelled 👍\n\nWhat else can I help with?"
            )
            return

        # ── 4e. Rate limiting (atomic Redis + Supabase fallback) ─
        # Try atomic Redis first; fall back to Supabase if Redis unavailable
        if not check_rate_limit_atomic(wa_id, DAILY_MESSAGE_LIMIT, settings):
            logger.info(f"[{request_id}] Rate limited {wa_id}")
            await _wa().send_text_message(
                wa_id,
                "You've reached today's limit 🙏\nCome back tomorrow or try again later!"
            )
            return

        # Still call Supabase rate limit to keep counters updated for analytics
        check_rate_limit(wa_id, settings)

        # ── 4f. First-time user onboarding (Supabase-backed) ────
        is_new_user = is_first_time_user(wa_id, name, settings)
        if is_new_user:
            greetings = ["hi", "hello", "hey", "hola", "namaste", "start", "help"]
            if msg_lower in greetings or len(msg_lower) < 5:
                logger.info(f"[{request_id}] New user welcome for {wa_id}")
                welcome = (
                    f"Hi {name}! I'm Mira 😊\n\n"
                    "I help the Indian community in the US find what they need — fast.\n\n"
                    "Try asking me:\n"
                    "👉 _\"Indian grocery near me\"_\n"
                    "👉 _\"deals in Columbus\"_\n"
                    "👉 _\"babysitter near me\"_\n\n"
                    "🏪 Own a business? Type *\"add my business\"* to get listed FREE!\n\n"
                    "📰 Want daily updates? Type *\"daily digest in [your city]\"*"
                )
                await _wa().send_text_message(wa_id, welcome)
                return

        # ── 4g. Active session check (BEFORE greeting) ──────────
        from app.services.business_registration import (
            detect_registration_intent,
            has_active_session,
            handle_registration_message,
            start_add_flow,
            start_update_flow,
        )

        if has_active_session(wa_id, settings):
            logger.info(f"[{request_id}] Continuing registration session for {wa_id}")
            response_text = handle_registration_message(wa_id, message_body, settings)
            await _wa().send_text_message(wa_id, response_text)
            return

        from app.services.deals_service import (
            detect_deal_intent,
            detect_more_deals_intent,
            detect_delete_deal_intent,
            detect_boost_intent,
            detect_boost_help_intent,
            delete_deal,
            boost_deal,
            handle_boost_help,
            has_active_deal_session,
            handle_deal_message,
            start_deal_flow,
            search_deals,
            format_deals_for_whatsapp,
            get_user_deal_offset,
            increment_user_deal_offset,
            reset_user_deal_offset,
        )

        if has_active_deal_session(wa_id, settings):
            logger.info(f"[{request_id}] Continuing deal session for {wa_id}")
            response_text = handle_deal_message(wa_id, message_body, settings)
            await _wa().send_text_message(wa_id, response_text)
            return

        from app.services.monetization_service import (
            detect_monetization_intent,
            has_active_upgrade_session,
            handle_upgrade_message,
            start_upgrade_flow,
            get_business_stats,
            get_plan_status,
            get_notification_history,
        )

        if has_active_upgrade_session(wa_id, settings):
            logger.info(f"[{request_id}] Continuing upgrade session for {wa_id}")
            response_text = handle_upgrade_message(wa_id, message_body, settings)
            await _wa().send_text_message(wa_id, response_text)
            return

        # ── 4h. Returning user greeting (AFTER session check) ───
        greetings = {"hi", "hello", "hey", "hola", "namaste", "start", "help"}
        if msg_lower in greetings:
            user_ctx = get_user_context(wa_id, settings)
            if user_ctx:
                stored_name = user_ctx.get("name") or name
                logger.info(f"[{request_id}] Returning user greeting for {wa_id}")
                welcome_back = (
                    f"Welcome back, {stored_name}! 👋\n\n"
                    "What can I help you with today?\n\n"
                    "🔍 Search for businesses or services\n"
                    "🏪 *\"add my business\"* — list your business\n"
                    "📊 *\"my stats\"* — see your business performance\n"
                    "💎 *\"upgrade\"* — boost your listing\n"
                    "📰 *\"daily digest in [city]\"* — get daily updates"
                )
                await _wa().send_text_message(wa_id, welcome_back)
                return

        # ── 4i. Intent routing ──────────────────────────────────

        # Registration intents
        reg_intent = detect_registration_intent(message_body)
        if reg_intent == "add":
            logger.info(f"[{request_id}] Starting add-business flow for {wa_id}")
            response_text = start_add_flow(wa_id, settings)
            await _wa().send_text_message(wa_id, response_text)
            return
        elif reg_intent == "update":
            logger.info(f"[{request_id}] Starting update-business flow for {wa_id}")
            response_text = start_update_flow(wa_id, settings)
            await _wa().send_text_message(wa_id, response_text)
            return

        # Deal intents
        deal_intent = detect_deal_intent(message_body)
        if deal_intent == "post":
            logger.info(f"[{request_id}] Starting post-deal flow for {wa_id}")
            # Attribution: was this triggered by a proof message?
            from app.services.proof_message_service import track_proof_action
            track_proof_action(wa_id, "post_deal", settings)
            response_text = start_deal_flow(wa_id, settings)
            await _wa().send_text_message(wa_id, response_text)
            return
        elif deal_intent == "browse_today":
            logger.info(f"[{request_id}] Browsing today's deals for {wa_id}")
            reset_user_deal_offset(wa_id, settings)
            deals = search_deals(message_body, settings, limit=5, today_only=True)
            response_text = format_deals_for_whatsapp(deals, query_type="today")
            await _wa().send_text_message(wa_id, response_text)
            return
        elif deal_intent == "browse":
            logger.info(f"[{request_id}] Browsing deals for {wa_id}")
            reset_user_deal_offset(wa_id, settings)
            deals = search_deals(message_body, settings, limit=5)
            response_text = format_deals_for_whatsapp(deals)
            await _wa().send_text_message(wa_id, response_text)
            return

        # "More deals" pagination (persistent offset)
        if detect_more_deals_intent(message_body):
            offset = increment_user_deal_offset(wa_id, settings, step=5)
            logger.info(f"[{request_id}] Showing more deals for {wa_id} (offset={offset})")
            deals = search_deals(message_body, settings, limit=5, offset=offset)
            if not deals:
                reset_user_deal_offset(wa_id, settings)
                await _wa().send_text_message(wa_id, "That's all the deals for now! Say *'show deals'* to start over, or try a different city. 🙏")
            else:
                response_text = format_deals_for_whatsapp(deals)
                await _wa().send_text_message(wa_id, response_text)
            return

        # Boost deal
        if detect_boost_intent(message_body):
            logger.info(f"[{request_id}] Boost deal request from {wa_id}")
            from app.services.proof_message_service import track_proof_action
            track_proof_action(wa_id, "boost", settings)
            response_text = boost_deal(wa_id, settings)
            await _wa().send_text_message(wa_id, response_text)
            return

        # Boost help (manual recovery if webhook failed)
        if detect_boost_help_intent(message_body):
            logger.info(f"[{request_id}] Boost help request from {wa_id}")
            response_text = handle_boost_help(wa_id, settings)
            await _wa().send_text_message(wa_id, response_text)
            return

        # Deal deletion
        if detect_delete_deal_intent(message_body):
            logger.info(f"[{request_id}] Deal deletion request from {wa_id}")
            search_term = message_body.lower().replace("delete deal", "").replace("remove deal", "").replace("delete my deal", "").replace("remove my deal", "").replace("cancel deal", "").replace("cancel my deal", "").strip()
            response_text = delete_deal(wa_id, search_term, settings)
            await _wa().send_text_message(wa_id, response_text)
            return

        # Digest reply (numbered quick replies: 1, 2, 3)
        from app.services.digest_service import (
            detect_digest_intent,
            detect_digest_reply,
            handle_digest_reply,
            subscribe_to_digest,
            unsubscribe_from_digest,
        )

        digest_reply_index = detect_digest_reply(message_body)
        if digest_reply_index:
            response_text = handle_digest_reply(wa_id, digest_reply_index, settings)
            if response_text:
                logger.info(f"[{request_id}] Digest reply #{digest_reply_index} from {wa_id}")
                await _wa().send_text_message(wa_id, response_text)
                return
            # If no cached digest, fall through to normal flow

        # Digest subscription

        digest_intent = detect_digest_intent(message_body)
        if digest_intent == "unsubscribe":
            logger.info(f"[{request_id}] Unsubscribing digest for {wa_id}")
            response_text = unsubscribe_from_digest(wa_id, settings)
            await _wa().send_text_message(wa_id, response_text)
            return
        elif digest_intent == "subscribe":
            logger.info(f"[{request_id}] Subscribing digest for {wa_id}")
            msg_lower_full = message_body.lower()
            city = ""
            for prep in ["in ", "for "]:
                if prep in msg_lower_full:
                    parts = msg_lower_full.split(prep, 1)
                    if len(parts) > 1:
                        city = parts[1].strip().rstrip(".")
                        break
            if not city:
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

            await _wa().send_text_message(wa_id, response_text)
            return

        # Weekly report
        report_phrases = ["my weekly report", "weekly report", "my report", "proof message"]
        if msg_lower in report_phrases:
            logger.info(f"[{request_id}] Weekly report for {wa_id}")
            from app.services.proof_message_service import send_proof_message_single
            result = await send_proof_message_single(wa_id, settings)
            if result:
                await _wa().send_text_message(wa_id, result)
            return

        # Monetization intents
        money_intent = detect_monetization_intent(message_body)
        if money_intent == "upgrade":
            logger.info(f"[{request_id}] Starting upgrade flow for {wa_id}")
            from app.services.proof_message_service import track_proof_action
            track_proof_action(wa_id, "upgrade", settings)
            response_text = start_upgrade_flow(wa_id, settings)
            await _wa().send_text_message(wa_id, response_text)
            return
        elif money_intent == "leads":
            logger.info(f"[{request_id}] Showing leads for {wa_id}")
            response_text = get_notification_history(wa_id, settings)
            await _wa().send_text_message(wa_id, response_text)
            return
        elif money_intent == "stats":
            logger.info(f"[{request_id}] Showing stats for {wa_id}")
            response_text = get_business_stats(wa_id, settings)
            await _wa().send_text_message(wa_id, response_text)
            return
        elif money_intent == "plan":
            logger.info(f"[{request_id}] Showing plan for {wa_id}")
            response_text = get_plan_status(wa_id, settings)
            await _wa().send_text_message(wa_id, response_text)
            return

        # ── 4j. LLM fallback (multi-tier router) ────────────────
        logger.info(f"[{request_id}] Falling through to LLM router for {wa_id}")
        from app.services.llm_router import generate_response

        response_text = await generate_response(
            message=message_body,
            wa_id=wa_id,
            name=name,
            settings=settings,
        )

        await _wa().send_text_message(wa_id, response_text)
        logger.info(f"[{request_id}] Request complete for {wa_id}")

    except Exception as e:
        logger.error(f"[{request_id}] Unhandled error processing message for {wa_id}: {e}")
        try:
            await _wa().send_text_message(
                wa_id,
                "Oops, something went wrong on my end. Please try again! 🙏"
            )
        except Exception:
            pass
    finally:
        # Always release the per-user lock
        release_user_lock(wa_id, settings)
