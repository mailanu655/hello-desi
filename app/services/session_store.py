"""
Mira — Redis-backed Session Store

Replaces in-memory dicts for multi-step WhatsApp conversation flows.
Sessions survive Railway container restarts and deploys.

Uses Upstash Redis with automatic TTL expiry.
Falls back to in-memory dict if Redis is unavailable (dev mode).
"""

import json
import logging
from typing import Any

import redis

from config.settings import Settings

logger = logging.getLogger(__name__)

# Session TTL: 15 minutes (refreshed on every interaction)
SESSION_TTL = 900

# Module-level Redis client (lazy-initialized)
_redis_client: redis.Redis | None = None
_redis_available: bool | None = None

# In-memory fallback (for dev/testing when Redis is down)
_fallback_store: dict[str, dict] = {}


def _get_redis(settings: Settings) -> redis.Redis | None:
    """Get or create a Redis connection. Returns None if unavailable."""
    global _redis_client, _redis_available

    # If we already know Redis is unavailable, skip
    if _redis_available is False:
        return None

    if _redis_client is not None:
        return _redis_client

    redis_url = getattr(settings, "REDIS_URL", "")
    if not redis_url:
        logger.info("REDIS_URL not configured — using in-memory fallback")
        _redis_available = False
        return None

    try:
        _redis_client = redis.from_url(
            redis_url,
            decode_responses=True,
            socket_connect_timeout=5,
            socket_timeout=5,
        )
        # Test connectivity
        _redis_client.ping()
        _redis_available = True
        logger.info("Redis session store connected")
        return _redis_client
    except Exception as e:
        logger.warning(f"Redis unavailable, falling back to in-memory: {e}")
        _redis_available = False
        _redis_client = None
        return None


def get_session(key: str, settings: Settings) -> dict | None:
    """
    Retrieve a session by key.

    Returns the session data dict, or None if not found / expired.
    Automatically refreshes TTL on access.
    """
    r = _get_redis(settings)

    if r:
        try:
            data = r.get(key)
            if data:
                r.expire(key, SESSION_TTL)  # Refresh TTL on every access
                return json.loads(data)
            return None
        except Exception as e:
            logger.warning(f"Redis get failed for {key}: {e}")
            # Fall through to in-memory
            return _fallback_store.get(key)
    else:
        return _fallback_store.get(key)


def set_session(key: str, data: dict, settings: Settings) -> None:
    """
    Store a session with automatic TTL.

    The session will expire after SESSION_TTL seconds of inactivity.
    Every call to get_session or set_session refreshes the TTL.
    """
    r = _get_redis(settings)

    if r:
        try:
            r.setex(key, SESSION_TTL, json.dumps(data, default=str))
            return
        except Exception as e:
            logger.warning(f"Redis set failed for {key}: {e}")
            # Fall through to in-memory

    _fallback_store[key] = data


def delete_session(key: str, settings: Settings) -> None:
    """Delete a session (on completion or cancellation)."""
    r = _get_redis(settings)

    if r:
        try:
            r.delete(key)
        except Exception as e:
            logger.warning(f"Redis delete failed for {key}: {e}")

    # Always clean up fallback too
    _fallback_store.pop(key, None)


def message_seen(message_id: str, settings: Settings) -> bool:
    """
    Check if a WhatsApp message ID has already been processed.

    Returns True if duplicate (already seen), False if new.
    Automatically marks the message as seen with a 5-min TTL.
    Prevents duplicate processing when WhatsApp retries webhook delivery.
    """
    if not message_id:
        return False  # No ID = can't deduplicate, process it

    key = f"msg:{message_id}"
    r = _get_redis(settings)

    if r:
        try:
            # SET NX = only set if not exists; returns True if set, False if already exists
            was_new = r.set(key, "1", ex=300, nx=True)
            return not was_new  # True if already existed (duplicate)
        except Exception as e:
            logger.warning(f"Redis dedup check failed for {message_id}: {e}")
            # Fall through to in-memory
            if key in _fallback_store:
                return True
            _fallback_store[key] = {"_dedup": True}
            return False
    else:
        if key in _fallback_store:
            return True
        _fallback_store[key] = {"_dedup": True}
        return False


def acquire_user_lock(wa_id: str, settings: Settings, ttl: int = 10) -> bool:
    """
    Acquire a per-user processing lock to prevent race conditions.

    Uses Redis SETNX for atomic lock acquisition. If the lock already exists
    (another request is processing for this user), returns False.

    The lock auto-expires after `ttl` seconds as a safety net.

    Returns True if lock acquired, False if another request holds it.
    """
    key = f"lock:{wa_id}"
    r = _get_redis(settings)

    if r:
        try:
            acquired = r.set(key, "1", nx=True, ex=ttl)
            return bool(acquired)
        except Exception as e:
            logger.warning(f"Redis lock acquire failed for {wa_id}: {e}")
            return True  # On failure, allow processing (don't block user)
    else:
        # In-memory: no real locking needed (single process)
        return True


def release_user_lock(wa_id: str, settings: Settings) -> None:
    """Release the per-user processing lock."""
    key = f"lock:{wa_id}"
    r = _get_redis(settings)

    if r:
        try:
            r.delete(key)
        except Exception as e:
            logger.warning(f"Redis lock release failed for {wa_id}: {e}")

    # Also clean up fallback
    _fallback_store.pop(key, None)


def check_rate_limit_atomic(wa_id: str, daily_limit: int, settings: Settings) -> bool:
    """
    Atomic rate limit check using Redis INCR.

    Returns True if user is WITHIN limits (allowed), False if rate-limited.
    Uses a key that auto-expires at midnight UTC (or after 24h).

    This replaces the read-then-write pattern in Supabase which had a race condition.
    Falls back to allowing the request if Redis is unavailable.
    """
    from datetime import datetime, timezone

    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    key = f"rate:{wa_id}:{today}"
    r = _get_redis(settings)

    if r:
        try:
            count = r.incr(key)
            if count == 1:
                # First message today — set TTL to 24 hours
                r.expire(key, 86400)
            return count <= daily_limit
        except Exception as e:
            logger.warning(f"Redis rate limit failed for {wa_id}: {e}")
            return True  # On failure, allow
    else:
        return True  # No Redis = no atomic rate limit, fall back to Supabase check


def check_burst_limit(wa_id: str, settings: Settings, per_minute: int = 10) -> bool:
    """
    Per-minute burst limiter using Redis INCR.

    Returns True if user is WITHIN burst limits (allowed).
    Returns False if user is sending too fast (> per_minute msgs in 60s).
    Prevents API cost spikes from rapid-fire messaging.
    """
    key = f"burst:{wa_id}"
    r = _get_redis(settings)

    if r:
        try:
            count = r.incr(key)
            if count == 1:
                r.expire(key, 60)  # 60-second window
            return count <= per_minute
        except Exception as e:
            logger.warning(f"Redis burst limit failed for {wa_id}: {e}")
            return True
    else:
        return True  # No Redis = no burst limit


def get_burst_count(wa_id: str, settings: Settings) -> int:
    """Get current burst count for soft throttle warning."""
    key = f"burst:{wa_id}"
    r = _get_redis(settings)
    if r:
        try:
            count = r.get(key)
            return int(count) if count else 0
        except Exception:
            return 0
    return 0


def get_tokens_today(wa_id: str, settings: Settings) -> int:
    """Get total LLM tokens used by this user today."""
    from datetime import datetime, timezone
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    key = f"tokens:{wa_id}:{today}"
    r = _get_redis(settings)
    if r:
        try:
            count = r.get(key)
            return int(count) if count else 0
        except Exception:
            return 0
    return 0


def get_daily_message_count(wa_id: str, settings: Settings) -> int:
    """
    Get current daily message count for grace warning logic.
    Returns 0 if Redis unavailable or key doesn't exist.
    """
    from datetime import datetime, timezone

    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    key = f"rate:{wa_id}:{today}"
    r = _get_redis(settings)

    if r:
        try:
            count = r.get(key)
            return int(count) if count else 0
        except Exception:
            return 0
    return 0


def get_user_daily_limit(wa_id: str, settings: Settings) -> int:
    """
    Get the daily message limit for a user based on their type.

    Tiers:
      - Premium subscriber: 200
      - Business owner (has registered business): 100
      - Normal user: 50
    """
    try:
        from supabase import create_client
        client = create_client(settings.SUPABASE_URL, settings.SUPABASE_KEY)

        # Check for active premium subscription first
        sub = (
            client.table("subscriptions")
            .select("plan")
            .eq("wa_id", wa_id)
            .eq("status", "active")
            .limit(1)
            .execute()
        )
        if sub.data:
            plan = sub.data[0].get("plan", "")
            if plan in ("premium", "featured"):
                return 200

        # Check if business owner
        biz = (
            client.table("businesses")
            .select("id")
            .ilike("source_id", f"%{wa_id}%")
            .limit(1)
            .execute()
        )
        if biz.data:
            return 100

    except Exception as e:
        logger.warning(f"Failed to determine user tier for {wa_id}: {e}")

    return 50  # Default: normal user


def track_token_usage(wa_id: str, tokens: int, settings: Settings) -> None:
    """
    Track LLM token usage per user per day.
    Used to detect expensive users and optimize routing.
    """
    from datetime import datetime, timezone

    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    key = f"tokens:{wa_id}:{today}"
    r = _get_redis(settings)

    if r:
        try:
            r.incrby(key, tokens)
            r.expire(key, 86400)
        except Exception:
            pass  # Non-critical — best effort


def session_exists(key: str, settings: Settings) -> bool:
    """Check if a session exists (without refreshing TTL)."""
    r = _get_redis(settings)

    if r:
        try:
            return bool(r.exists(key))
        except Exception as e:
            logger.warning(f"Redis exists check failed for {key}: {e}")
            return key in _fallback_store
    else:
        return key in _fallback_store
