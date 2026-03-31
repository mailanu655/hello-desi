"""
Mira — Claude AI Integration Service (v3 — hardened)

Changes from v2:
- History contamination guard (don't store error replies)
- Skip history when user is in an active session flow
- Context injection budget cap (1200 chars)
- Response cache for repeated queries (Redis, 5min TTL)
- Structured token logging for cost visibility

Carried from v2:
- AsyncAnthropic singleton (non-blocking, reuses connection pool)
- Right-sized max_tokens (300 Haiku / 512 Sonnet)
- Input length guard (1500 char cap)
- Output char clamp (1200 chars for WhatsApp)
- Conditional DB search (only for local queries)
- Redis-backed conversation history (last 3 turns)
- Trimmed system prompt (~150 tokens)
"""

import hashlib
import json
import logging
from typing import Any

import anthropic

from app.services.business_service import search_businesses, format_businesses_for_prompt, NO_RESULTS_MESSAGE
from app.utils.whatsapp_utils import process_text_for_whatsapp
from config.settings import Settings

logger = logging.getLogger(__name__)

# ── Singleton async client (lazy-initialized per API key) ───────────
_async_clients: dict[str, anthropic.AsyncAnthropic] = {}


def _get_client(settings: Settings, timeout: float = 8.0) -> anthropic.AsyncAnthropic:
    """
    Return a singleton AsyncAnthropic client.

    Reuses connection pool across requests. One client per API key.
    Timeout is set per-request via the API call, not the client.
    """
    key = settings.ANTHROPIC_API_KEY
    if key not in _async_clients:
        _async_clients[key] = anthropic.AsyncAnthropic(api_key=key)
        logger.info("AsyncAnthropic client initialized")
    return _async_clients[key]


# ── Model configuration ─────────────────────────────────────────────
HAIKU_MODEL = "claude-haiku-4-5-20251001"
SONNET_MODEL = "claude-sonnet-4-5-20241022"

MAX_TOKENS_HAIKU = 300    # ~800 chars — perfect for WhatsApp
MAX_TOKENS_SONNET = 512   # Longer for complex immigration/finance answers
TIMEOUT_HAIKU = 8.0       # seconds
TIMEOUT_SONNET = 15.0     # seconds

# ── Input/output/context guards ──────────────────────────────────────
MAX_INPUT_CHARS = 1500    # Prevents expensive token bills from long messages
MAX_OUTPUT_CHARS = 1200   # WhatsApp readability limit (with some buffer)
MAX_CONTEXT_CHARS = 1200  # Cap injected business/deals context to prevent token creep
CACHE_TTL = 300           # 5 min TTL for response cache

# Error phrases that should NOT be stored in conversation history
ERROR_PHRASES = [
    "Sorry, I'm having trouble",
    "Oops, something went wrong",
    "That took too long",
]


# ── Trimmed system prompt (~150 tokens vs ~450 before) ──────────────
# Removed: command lists (handled by intent router), verbose style guide,
# signature phrases, detailed "what you help with" list
SYSTEM_PROMPT = """You are Mira — a friendly, helpful WhatsApp assistant for the Indian community in the USA.

RULES:
- Keep responses SHORT (under 6 lines). This is WhatsApp, not email.
- Use emojis sparingly. Sound like a helpful desi friend, not a robot.
- Match the user's language (English → English, Hindi/Hinglish → match their style).
- If showing options, show 2-3 max with key details.
- End with a follow-up: "Want more?" or "Need directions?"
- Immigration/finance → always add: "⚠️ General info only — consult a professional."
- Never invent business listings — only use data provided in context.
- If you don't know, say so honestly.
"""


# ── Keywords for model routing and disclaimers ──────────────────────
COMPLEX_KEYWORDS = [
    "immigration", "visa", "h1b", "h-1b", "h1-b", "green card", "uscis",
    "eb2", "eb-2", "eb3", "eb-3", "i-485", "i-140", "i-130", "i-765",
    "ead", "advance parole", "opt", "cpt", "perm", "labor cert",
    "priority date", "visa bulletin", "rfe", "noid",
    "nre", "nro", "tax", "investment", "remittance", "forex",
    "financial", "capital gains", "401k", "ira", "fbar", "fatca",
    "wire transfer", "exchange rate", "legal",
]

IMMIGRATION_KEYWORDS = [
    "immigration", "visa", "h1b", "h-1b", "h1-b", "green card", "uscis",
    "eb2", "eb-2", "eb3", "eb-3", "i-485", "i-140", "i-130", "i-765",
    "ead", "ap ", "advance parole", "opt", "cpt", "perm", "labor cert",
    "priority date", "visa bulletin", "rfe", "noid",
]
FINANCE_KEYWORDS = [
    "nre", "nro", "tax", "investment", "remittance", "forex",
    "financial", "capital gains", "401k", "ira", "fbar", "fatca",
    "wire transfer", "exchange rate",
]

IMMIGRATION_DISCLAIMER = "\n\n⚠️ _General info only — please consult an immigration attorney._"
FINANCE_DISCLAIMER = "\n\n⚠️ _General info only — please consult a financial advisor._"

# ── Local search detection (skip DB lookup for non-local queries) ───
LOCAL_SIGNALS = [
    "near", "near me", "nearby", "around", "closest",
    "grocery", "restaurant", "tiffin", "salon", "nanny", "babysitter",
    "temple", "mandir", "doctor", "lawyer", "cpa", "realtor", "dentist",
    "jeweler", "banquet", "insurance", "travel agent",
    "deal", "deals",
]


def _looks_local(message: str) -> bool:
    """Check if the message looks like a local business/deals search."""
    msg = message.lower()
    return any(kw in msg for kw in LOCAL_SIGNALS)


def _is_complex(message: str) -> bool:
    """Check if the message needs Sonnet (immigration, finance, legal)."""
    msg = message.lower()
    return any(kw in msg for kw in COMPLEX_KEYWORDS)


def _clamp_input(text: str) -> str:
    """Truncate user input to prevent expensive token bills."""
    if len(text) <= MAX_INPUT_CHARS:
        return text
    logger.warning(f"Input clamped from {len(text)} to {MAX_INPUT_CHARS} chars")
    return text[:MAX_INPUT_CHARS]


def _clamp_output(text: str) -> str:
    """Truncate output for WhatsApp readability."""
    if len(text) <= MAX_OUTPUT_CHARS:
        return text
    # Find the last sentence boundary before the limit
    truncated = text[:MAX_OUTPUT_CHARS]
    last_period = truncated.rfind(".")
    last_newline = truncated.rfind("\n")
    cut_point = max(last_period, last_newline)
    if cut_point > MAX_OUTPUT_CHARS // 2:
        return truncated[:cut_point + 1]
    return truncated + "…"


def _enforce_disclaimers(user_message: str, response: str) -> str:
    """
    Programmatically enforce disclaimers on immigration/finance responses.
    If Claude already included a disclaimer, skip. Otherwise append one.
    """
    msg_lower = user_message.lower()
    resp_lower = response.lower()

    is_immigration = any(kw in msg_lower for kw in IMMIGRATION_KEYWORDS)
    is_finance = any(kw in msg_lower for kw in FINANCE_KEYWORDS)

    has_disclaimer = (
        "consult" in resp_lower
        or "not legal advice" in resp_lower
        or "not financial advice" in resp_lower
        or "general info only" in resp_lower
        or "professional advice" in resp_lower
    )

    if is_immigration and not has_disclaimer:
        response += IMMIGRATION_DISCLAIMER
    elif is_finance and not has_disclaimer:
        response += FINANCE_DISCLAIMER

    return response


def _should_store_in_history(reply: str) -> bool:
    """Check if a reply is clean enough to store in conversation history."""
    if len(reply) < 10:
        return False
    return not any(phrase in reply for phrase in ERROR_PHRASES)


def _cache_key(message: str, name: str) -> str:
    """Generate a deterministic cache key for a query."""
    raw = f"{message.lower().strip()}:{name}"
    return f"llm:{hashlib.md5(raw.encode()).hexdigest()[:12]}"


async def _get_cached_response(message: str, name: str, settings: Settings) -> str | None:
    """Check if we have a cached LLM response for this query."""
    try:
        from app.services.session_store import _get_redis
        r = _get_redis(settings)
        if r:
            data = r.get(_cache_key(message, name))
            if data:
                logger.info(f"Cache HIT for query: {message[:30]}...")
                return data
    except Exception as e:
        logger.warning(f"Cache read failed: {e}")
    return None


async def _cache_response(message: str, name: str, response: str, settings: Settings) -> None:
    """Cache an LLM response for repeated queries."""
    try:
        from app.services.session_store import _get_redis
        r = _get_redis(settings)
        if r:
            r.setex(_cache_key(message, name), CACHE_TTL, response)
    except Exception as e:
        logger.warning(f"Cache write failed: {e}")


# ── Conversation history (Redis-backed) ─────────────────────────────
HISTORY_TTL = 3600  # 1 hour — conversations don't last longer on WhatsApp
MAX_HISTORY_TURNS = 6  # 3 user + 3 assistant messages


async def _get_history(wa_id: str, settings: Settings) -> list[dict]:
    """Retrieve conversation history from Redis."""
    try:
        from app.services.session_store import _get_redis
        r = _get_redis(settings)
        if r:
            data = r.get(f"hist:{wa_id}")
            if data:
                r.expire(f"hist:{wa_id}", HISTORY_TTL)
                return json.loads(data)
    except Exception as e:
        logger.warning(f"History retrieval failed for {wa_id}: {e}")
    return []


async def _save_history(
    wa_id: str, user_msg: str, assistant_msg: str, settings: Settings
) -> None:
    """
    Append to conversation history in Redis. Keeps last N turns.

    Skips storing error/fallback replies to prevent bad context accumulation.
    """
    # History contamination guard — don't store error replies
    if not _should_store_in_history(assistant_msg):
        logger.info(f"Skipping history save for {wa_id} — error reply")
        return

    try:
        from app.services.session_store import _get_redis
        r = _get_redis(settings)
        if not r:
            return

        hist = []
        data = r.get(f"hist:{wa_id}")
        if data:
            hist = json.loads(data)

        hist.append({"role": "user", "content": user_msg})
        hist.append({"role": "assistant", "content": assistant_msg})

        # Keep only the last N messages
        hist = hist[-MAX_HISTORY_TURNS:]

        r.setex(f"hist:{wa_id}", HISTORY_TTL, json.dumps(hist))
    except Exception as e:
        logger.warning(f"History save failed for {wa_id}: {e}")


# ── Main response generation ────────────────────────────────────────

async def generate_response(
    message: str,
    wa_id: str,
    name: str,
    settings: Settings,
    conversation_history: list[dict] | None = None,
    include_history: bool = True,
) -> str:
    """
    Generate an AI response using Claude.

    Args:
        include_history: If False, skip loading conversation history.
                         Set to False when user is in an active session flow
                         to prevent history from interfering with flow logic.

    Optimized pipeline:
    1. Clamp input length
    2. Check response cache (skip LLM call if hit)
    3. Select model (Haiku default, Sonnet for complex topics + long input)
    4. Conditionally search businesses/deals (only for local queries)
    5. Load conversation history from Redis (if enabled)
    6. Call Claude (async, right-sized max_tokens)
    7. Enforce disclaimers
    8. Clamp output + format for WhatsApp
    9. Save conversation history + cache response
    """
    model = HAIKU_MODEL  # Default for error handlers

    try:
        # ── 1. Input guard ──────────────────────────────────────
        message = _clamp_input(message)

        # ── 2. Check response cache ─────────────────────────────
        cached = await _get_cached_response(message, name, settings)
        if cached:
            return cached

        # ── 3. Model selection (keywords + length heuristic) ────
        use_sonnet = _is_complex(message) or len(message) > 300
        model = SONNET_MODEL if use_sonnet else HAIKU_MODEL
        max_tokens = MAX_TOKENS_SONNET if use_sonnet else MAX_TOKENS_HAIKU
        timeout = TIMEOUT_SONNET if use_sonnet else TIMEOUT_HAIKU

        if use_sonnet:
            logger.info(f"Escalating to Sonnet for complex query from {wa_id}")

        # ── 4. Conditional DB search (only for local queries) ───
        business_context = ""
        deals_context = ""

        if _looks_local(message):
            try:
                results = search_businesses(message, settings, limit=5, wa_id=wa_id)
                if results:
                    business_context = format_businesses_for_prompt(results)
                    logger.info(f"Found {len(results)} businesses for query from {wa_id}")
                    try:
                        from app.services.monetization_service import log_inquiry
                        log_inquiry(results, wa_id, "search", message, settings)
                    except Exception as inq_err:
                        logger.warning(f"Inquiry logging failed: {inq_err}")
                else:
                    # No results — return explicit helpful message instead of
                    # letting Claude guess (which may hallucinate listings)
                    logger.info(f"No business results for query from {wa_id}")
                    return NO_RESULTS_MESSAGE
            except Exception as e:
                logger.warning(f"Business lookup failed for {wa_id}: {e}")

            try:
                from app.services.deals_service import search_deals, format_deals_for_prompt
                deals = search_deals(message, settings, limit=3)
                if deals:
                    deals_context = format_deals_for_prompt(deals)
                    logger.info(f"Found {len(deals)} deals for query from {wa_id}")
            except Exception as e:
                logger.warning(f"Deal lookup failed for {wa_id}: {e}")

        # ── 5. Load conversation history (if enabled) ───────────
        if include_history:
            messages = await _get_history(wa_id, settings)
        else:
            messages = []
        messages.append({"role": "user", "content": message})

        # ── 6. Build system prompt (context budget cap) ─────────
        system_parts = [SYSTEM_PROMPT, f"\nThe user's name is {name}."]

        # Apply context budget cap
        combined_context = business_context + deals_context
        if combined_context:
            if len(combined_context) > MAX_CONTEXT_CHARS:
                combined_context = combined_context[:MAX_CONTEXT_CHARS]
                logger.info(f"Context capped at {MAX_CONTEXT_CHARS} chars for {wa_id}")
            system_parts.append(combined_context)

        system_msg = "".join(system_parts)

        # ── 7. Call Claude (async + right-sized) ────────────────
        client = _get_client(settings)
        response = await client.messages.create(
            model=model,
            max_tokens=max_tokens,
            system=system_msg,
            messages=messages,
            timeout=timeout,
        )

        response_text = response.content[0].text

        # ── 8. Post-processing ──────────────────────────────────
        response_text = _enforce_disclaimers(message, response_text)
        formatted = process_text_for_whatsapp(response_text)
        formatted = _clamp_output(formatted)

        # ── Token logging (cost visibility) ─────────────────────
        input_tokens = response.usage.input_tokens
        output_tokens = response.usage.output_tokens
        logger.info(
            f"Claude response for {wa_id} | "
            f"model={model} | "
            f"chars={len(formatted)} | "
            f"in_tok={input_tokens} | "
            f"out_tok={output_tokens} | "
            f"cache={'miss' if not cached else 'hit'} | "
            f"history={'on' if include_history else 'off'}"
        )

        # ── 9. Save history + cache response ────────────────────
        if include_history:
            await _save_history(wa_id, message, formatted, settings)
        await _cache_response(message, name, formatted, settings)

        return formatted

    except anthropic.APITimeoutError:
        logger.warning(f"Claude timeout for {wa_id} (model={model})")
        return "That took too long 😅 Try a shorter question or try again!"
    except anthropic.APIError as e:
        logger.error(f"Claude API error for {wa_id}: {e}")
        return (
            "Sorry, I'm having trouble right now. Please try again in a moment! 🙏"
        )
    except Exception as e:
        logger.error(f"Unexpected error generating response for {wa_id}: {e}")
        return (
            "Oops, something went wrong on my end. Please try again! 🙏"
        )
