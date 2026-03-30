"""
Mira — Persistent User State Service

Replaces in-memory _seen_users, session state, and rate limiting with
Supabase-backed storage that survives Render restarts.

Tables used:
  - user_state: tracks seen status, city, first_seen, last_active, message_count
  - notification_log: logs lead notification outcomes for reliability tracking
"""

import logging
import uuid
from datetime import datetime, timezone

from supabase import create_client
from config.settings import Settings

logger = logging.getLogger(__name__)

# ── In-memory cache (optional optimization to avoid DB hit on every message) ──
_seen_cache: set[str] = set()

# ── Daily message rate limit ──
DAILY_MESSAGE_LIMIT = 50  # generous for now; tighten later


def is_first_time_user(wa_id: str, name: str, settings: Settings) -> bool:
    """
    Check if this user has been seen before. If not, create their record.
    Uses Supabase user_state table with in-memory cache for performance.
    Returns True if this is their first message ever.
    """
    # Fast path: if we've seen them this session, skip DB
    if wa_id in _seen_cache:
        return False

    try:
        client = create_client(settings.SUPABASE_URL, settings.SUPABASE_KEY)

        # Check if user exists in DB
        result = (
            client.table("user_state")
            .select("wa_id")
            .eq("wa_id", wa_id)
            .limit(1)
            .execute()
        )

        if result.data:
            # Existing user — update last_active and increment message count
            _seen_cache.add(wa_id)
            client.table("user_state").update({
                "last_active": datetime.now(timezone.utc).isoformat(),
                "name": name,
            }).eq("wa_id", wa_id).execute()
            return False

        # New user — insert record
        client.table("user_state").insert({
            "wa_id": wa_id,
            "name": name,
            "first_seen": datetime.now(timezone.utc).isoformat(),
            "last_active": datetime.now(timezone.utc).isoformat(),
            "messages_today": 0,
            "message_date": datetime.now(timezone.utc).date().isoformat(),
        }).execute()
        _seen_cache.add(wa_id)
        return True

    except Exception as e:
        logger.warning(f"user_state check failed for {wa_id}, falling back to allow: {e}")
        # On DB failure, don't block the user — let them through
        _seen_cache.add(wa_id)
        return False


def get_user_context(wa_id: str, settings: Settings) -> dict | None:
    """
    Return stored user context for personalization.
    Returns dict with name, first_seen, last_active, messages_today or None.
    """
    try:
        client = create_client(settings.SUPABASE_URL, settings.SUPABASE_KEY)
        result = (
            client.table("user_state")
            .select("name, first_seen, last_active, messages_today")
            .eq("wa_id", wa_id)
            .limit(1)
            .execute()
        )
        if result.data:
            return result.data[0]
        return None
    except Exception as e:
        logger.warning(f"get_user_context failed for {wa_id}: {e}")
        return None


def check_rate_limit(wa_id: str, settings: Settings) -> bool:
    """
    Check if user has exceeded daily message limit.
    Returns True if the user is WITHIN limits (allowed to send).
    Returns False if rate-limited.
    Also increments the counter.
    """
    try:
        client = create_client(settings.SUPABASE_URL, settings.SUPABASE_KEY)
        today = datetime.now(timezone.utc).date().isoformat()

        result = (
            client.table("user_state")
            .select("messages_today, message_date")
            .eq("wa_id", wa_id)
            .limit(1)
            .execute()
        )

        if not result.data:
            return True  # Unknown user, allow

        row = result.data[0]
        msg_date = row.get("message_date", "")
        count = row.get("messages_today", 0)

        if msg_date != today:
            # New day — reset counter
            client.table("user_state").update({
                "messages_today": 1,
                "message_date": today,
                "last_active": datetime.now(timezone.utc).isoformat(),
            }).eq("wa_id", wa_id).execute()
            return True

        if count >= DAILY_MESSAGE_LIMIT:
            return False

        # Increment
        client.table("user_state").update({
            "messages_today": count + 1,
            "last_active": datetime.now(timezone.utc).isoformat(),
        }).eq("wa_id", wa_id).execute()
        return True

    except Exception as e:
        logger.warning(f"Rate limit check failed for {wa_id}: {e}")
        return True  # On failure, allow


def log_notification(
    business_id: str,
    business_name: str,
    owner_wa_id: str,
    search_query: str,
    status: str,
    error_msg: str = "",
    settings: Settings | None = None,
) -> None:
    """
    Log lead notification outcome for reliability tracking.
    status: "sent", "rate_limited", "no_owner", "failed"
    """
    if not settings:
        return
    try:
        client = create_client(settings.SUPABASE_URL, settings.SUPABASE_KEY)
        client.table("notification_log").insert({
            "id": str(uuid.uuid4()),
            "business_id": business_id,
            "business_name": business_name,
            "owner_wa_id": owner_wa_id,
            "search_query": search_query[:200] if search_query else "",
            "status": status,
            "error_msg": error_msg[:500] if error_msg else "",
        }).execute()
    except Exception as e:
        logger.warning(f"Failed to log notification: {e}")
