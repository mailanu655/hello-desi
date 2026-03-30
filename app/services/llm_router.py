"""
Mira — Multi-LLM Router (v1)

Routes queries to the cheapest capable model to cut costs 60-90%.

Three tiers:
  CHEAP  — Gemini Flash (direct or via OpenRouter)   ~70-80% of traffic
  MID    — Claude Haiku 4.5                          ~15-25% of traffic
  PREMIUM — Claude Sonnet 4.5                        ~5% of traffic

Cheap tier priority:
  1. Direct Gemini API (GEMINI_API_KEY) — fastest, cheapest
  2. OpenRouter (OPENROUTER_API_KEY) — fallback if no Gemini key

Routing logic:
  1. classify_query() → cheap / mid / premium
  2. Call the selected tier
  3. If cheap model → run quality check → fallback to Haiku if bad
  4. Enforce disclaimers, clamp output, cache, log

Fallback chain: cheap → mid → premium (never skip a tier)
"""

import hashlib
import json
import logging
import time
from typing import Literal

import httpx

from app.services.claude_service import (
    SYSTEM_PROMPT,
    COMPLEX_KEYWORDS,
    LOCAL_SIGNALS,
    IMMIGRATION_KEYWORDS,
    FINANCE_KEYWORDS,
    _clamp_input,
    _clamp_output,
    _enforce_disclaimers,
    _looks_local,
    _should_store_in_history,
    _get_history,
    _save_history,
    _get_cached_response,
    _cache_response,
    generate_response as claude_generate_response,
)
from app.services.business_service import search_businesses, format_businesses_for_prompt
from app.utils.whatsapp_utils import process_text_for_whatsapp
from config.settings import Settings

logger = logging.getLogger(__name__)

# ── Tier definitions ───────────────────────────────────────────────
Tier = Literal["cheap", "mid", "premium"]

# Direct Gemini API config
GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
GEMINI_MODEL = "gemini-2.0-flash-lite"
GEMINI_TIMEOUT = 8.0
GEMINI_MAX_TOKENS = 300

# OpenRouter config (fallback if no GEMINI_API_KEY)
OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
OPENROUTER_MODEL = "google/gemini-flash-1.5-8b"
OPENROUTER_TIMEOUT = 8.0
OPENROUTER_MAX_TOKENS = 300

# ── Query classification ──────────────────────────────────────────

# Queries that are simple enough for the cheap model
SIMPLE_PATTERNS = [
    "hi", "hello", "hey", "thanks", "thank you", "ok", "okay",
    "yes", "no", "bye", "good morning", "good night", "good evening",
    "what time", "how are you", "who are you", "what can you do",
]

# Queries needing business context (mid tier — needs good instruction following)
CONTEXT_SIGNALS = LOCAL_SIGNALS  # reuse from claude_service

# Complex topics always go to premium
PREMIUM_KEYWORDS = COMPLEX_KEYWORDS  # immigration, finance, legal


def classify_query(message: str) -> Tier:
    """
    Classify a user query into cheap / mid / premium tier.

    Rules:
      - Greetings, thanks, yes/no, simple questions → cheap
      - Local business search (needs DB context injection) → mid
      - Immigration, finance, legal, long queries → premium
      - Everything else (general questions) → cheap
    """
    msg = message.lower().strip()

    # ── Premium: complex topics ────────────────────────────────
    if any(kw in msg for kw in PREMIUM_KEYWORDS):
        return "premium"

    # ── Premium: long messages (likely need nuanced response) ──
    if len(message) > 400:
        return "premium"

    # ── Mid: local business queries (need context injection) ───
    if any(kw in msg for kw in CONTEXT_SIGNALS):
        return "mid"

    # ── Cheap: simple patterns ─────────────────────────────────
    if msg in SIMPLE_PATTERNS or len(msg) < 10:
        return "cheap"

    # ── Default: cheap (general knowledge, casual chat) ────────
    return "cheap"


# ── Quality detection (for cheap model responses) ─────────────────

# Signs the cheap model gave a bad response
BAD_RESPONSE_SIGNALS = [
    "i cannot",
    "i can't help",
    "as an ai",
    "i don't have access",
    "i'm not able to",
    "i apologize, but",
    "sorry, i cannot",
    "i'm unable to",
    "error",
    # Too short / empty
]


def is_low_quality(response: str, user_message: str) -> bool:
    """
    Heuristic check: did the cheap model give a usable response?

    Returns True if the response should be retried on a higher tier.
    """
    if not response or len(response.strip()) < 15:
        return True

    resp_lower = response.lower()

    # Check for refusal / inability patterns
    if any(sig in resp_lower for sig in BAD_RESPONSE_SIGNALS):
        return True

    # Response is just echoing the question back
    if user_message.lower().strip() in resp_lower and len(response) < 50:
        return True

    return False


# ── Direct Gemini API (preferred cheap tier) ──────────────────────

async def _call_gemini(
    message: str,
    name: str,
    system_msg: str,
    messages: list[dict],
    settings: Settings,
) -> str | None:
    """
    Call Gemini Flash directly via Google's generativeLanguage API.

    Preferred over OpenRouter — no middleman, lower latency, cheaper.
    Returns the response text, or None if the call fails.
    """
    api_key = getattr(settings, "GEMINI_API_KEY", "")
    if not api_key:
        return None

    url = GEMINI_URL.format(model=GEMINI_MODEL) + f"?key={api_key}"

    # Convert chat messages to Gemini format
    contents = []
    for msg in messages:
        role = "user" if msg["role"] == "user" else "model"
        contents.append({"role": role, "parts": [{"text": msg["content"]}]})

    payload = {
        "contents": contents,
        "systemInstruction": {"parts": [{"text": system_msg}]},
        "generationConfig": {
            "maxOutputTokens": GEMINI_MAX_TOKENS,
            "temperature": 0.7,
        },
    }

    try:
        async with httpx.AsyncClient(timeout=GEMINI_TIMEOUT) as client:
            resp = await client.post(url, json=payload)
            resp.raise_for_status()

            data = resp.json()
            text = data["candidates"][0]["content"]["parts"][0]["text"]

            # Log token usage
            usage = data.get("usageMetadata", {})
            logger.info(
                f"Gemini response | "
                f"model={GEMINI_MODEL} | "
                f"chars={len(text)} | "
                f"in_tok={usage.get('promptTokenCount', '?')} | "
                f"out_tok={usage.get('candidatesTokenCount', '?')}"
            )
            return text

    except httpx.TimeoutException:
        logger.warning("Gemini timeout — will try OpenRouter or fallback to mid tier")
        return None
    except httpx.HTTPStatusError as e:
        logger.warning(f"Gemini HTTP {e.response.status_code}: {e.response.text[:200]}")
        return None
    except Exception as e:
        logger.warning(f"Gemini error: {e}")
        return None


# ── OpenRouter (cheap tier fallback) ─────────────────────────────

async def _call_openrouter(
    message: str,
    name: str,
    system_msg: str,
    messages: list[dict],
    settings: Settings,
) -> str | None:
    """
    Call the cheap model via OpenRouter.

    Returns the response text, or None if the call fails.
    Never raises — all errors are caught and logged.
    """
    api_key = getattr(settings, "OPENROUTER_API_KEY", "")
    if not api_key:
        logger.debug("OpenRouter API key not configured — skipping cheap tier")
        return None

    payload = {
        "model": OPENROUTER_MODEL,
        "max_tokens": OPENROUTER_MAX_TOKENS,
        "messages": [
            {"role": "system", "content": system_msg},
            *messages,
        ],
    }

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "HTTP-Referer": "https://hellodesi.app",
        "X-Title": "Mira WhatsApp Bot",
    }

    try:
        async with httpx.AsyncClient(timeout=OPENROUTER_TIMEOUT) as client:
            resp = await client.post(OPENROUTER_URL, json=payload, headers=headers)
            resp.raise_for_status()

            data = resp.json()
            text = data["choices"][0]["message"]["content"]

            # Log token usage if available
            usage = data.get("usage", {})
            logger.info(
                f"OpenRouter response | "
                f"model={OPENROUTER_MODEL} | "
                f"chars={len(text)} | "
                f"in_tok={usage.get('prompt_tokens', '?')} | "
                f"out_tok={usage.get('completion_tokens', '?')}"
            )
            return text

    except httpx.TimeoutException:
        logger.warning("OpenRouter timeout — will fallback to mid tier")
        return None
    except httpx.HTTPStatusError as e:
        logger.warning(f"OpenRouter HTTP {e.response.status_code}: {e.response.text[:200]}")
        return None
    except Exception as e:
        logger.warning(f"OpenRouter error: {e}")
        return None


# ── Main router entry point ───────────────────────────────────────

async def generate_response(
    message: str,
    wa_id: str,
    name: str,
    settings: Settings,
    conversation_history: list[dict] | None = None,
    include_history: bool = True,
) -> str:
    """
    Generate a response using the cheapest capable model.

    Drop-in replacement for claude_service.generate_response().
    Same signature, same return type, same post-processing.

    Pipeline:
      1. Clamp input
      2. Check cache
      3. Classify query → tier
      4. If cheap tier + OpenRouter configured → try cheap model
         4a. Quality check → fallback to mid if bad
      5. Mid/premium → delegate to claude_service.generate_response()
      6. Post-process (disclaimers, clamp, format, cache, history)
    """
    start_time = time.monotonic()

    # ── 1. Input guard ──────────────────────────────────────────
    message = _clamp_input(message)

    # ── 2. Check response cache ─────────────────────────────────
    cached = await _get_cached_response(message, name, settings)
    if cached:
        logger.info(f"Router cache HIT for {wa_id}")
        return cached

    # ── 3. Classify query ───────────────────────────────────────
    tier = classify_query(message)
    logger.info(f"Router classified query from {wa_id} as '{tier}': {message[:50]}...")

    # ── 4. Try cheap tier (Gemini direct → OpenRouter fallback) ──
    if tier == "cheap":
        gemini_key = getattr(settings, "GEMINI_API_KEY", "")
        openrouter_key = getattr(settings, "OPENROUTER_API_KEY", "")

        if gemini_key or openrouter_key:
            # Build system prompt (no business context for cheap queries)
            system_msg = SYSTEM_PROMPT + f"\nThe user's name is {name}."

            # Load conversation history
            if include_history:
                messages = await _get_history(wa_id, settings)
            else:
                messages = []
            messages.append({"role": "user", "content": message})

            # Try Gemini direct first, then OpenRouter
            cheap_response = None
            model_used = None

            if gemini_key:
                cheap_response = await _call_gemini(
                    message=message, name=name,
                    system_msg=system_msg, messages=messages,
                    settings=settings,
                )
                if cheap_response:
                    model_used = GEMINI_MODEL

            if not cheap_response and openrouter_key:
                cheap_response = await _call_openrouter(
                    message=message, name=name,
                    system_msg=system_msg, messages=messages,
                    settings=settings,
                )
                if cheap_response:
                    model_used = OPENROUTER_MODEL

            if cheap_response and not is_low_quality(cheap_response, message):
                # ── Post-process ────────────────────────────────
                response_text = _enforce_disclaimers(message, cheap_response)
                formatted = process_text_for_whatsapp(response_text)
                formatted = _clamp_output(formatted)

                elapsed = time.monotonic() - start_time
                logger.info(
                    f"Router response for {wa_id} | "
                    f"tier=cheap | model={model_used} | "
                    f"chars={len(formatted)} | "
                    f"time={elapsed:.2f}s | "
                    f"history={'on' if include_history else 'off'}"
                )

                # Save history + cache
                if include_history:
                    await _save_history(wa_id, message, formatted, settings)
                await _cache_response(message, name, formatted, settings)

                return formatted

            # Cheap model failed quality check → escalate to mid
            logger.info(
                f"Cheap model quality check failed for {wa_id} — "
                f"escalating to mid tier"
            )
            tier = "mid"

    # ── 5. Mid / Premium → delegate to Claude service ───────────
    # The claude_service already handles:
    #   - Haiku (mid) vs Sonnet (premium) routing via _is_complex()
    #   - Business context injection for local queries
    #   - Conversation history, caching, disclaimers, output clamping
    #
    # We just need to hint which model to use.
    # For "mid" tier, we force Haiku by keeping the message as-is.
    # For "premium" tier, claude_service already escalates via _is_complex().

    response = await claude_generate_response(
        message=message,
        wa_id=wa_id,
        name=name,
        settings=settings,
        include_history=include_history,
    )

    elapsed = time.monotonic() - start_time
    logger.info(
        f"Router response for {wa_id} | "
        f"tier={tier} | "
        f"time={elapsed:.2f}s"
    )

    return response
