"""
Mira — Daily Metro Digest Service (v2 — engagement-optimized)

An opt-in daily WhatsApp newsletter that delivers curated content
to users in their metro area. Core retention engine and revenue channel.

v2 improvements:
- Action-driven format with numbered quick replies (1, 2, 3)
- Boosted deals ALWAYS appear at positions 1-2
- Personalization: reorder by user's last searched category
- Digest click/reply tracking for preference learning
- Evening "expiring deals" push (5pm EST)
- Re-engagement nudge for passive subscribers
- Sponsored slot rotation for featured businesses

Format:
  🔥 *Top deals in Columbus TODAY*

  1. 🍛 20% off Biryani @ Hyderabad House
     ⏰ Ends today
     👉 Reply "1" for details

  2. 🛒 $10 off groceries @ Patel Brothers
     🚀 Boosted
     👉 Reply "2"

  3. 💈 Free consult @ Sharma Legal
     🆕 New this week
     👉 Reply "3"

  ⭐ *Sponsored*
  Taj Palace — Weekend brunch now open! 469-555-1234

  👉 Reply *more* for all deals
  Reply *stop digest* to unsubscribe
"""

import json
import logging
import random
from datetime import datetime, timedelta, timezone

from supabase import create_client
from config.settings import Settings

logger = logging.getLogger(__name__)


# ── Subscriber Management ────────────────────────────────────────

def subscribe_to_digest(wa_id: str, city: str, settings: Settings) -> str:
    """Opt a user into the daily digest for their city."""
    try:
        client = create_client(settings.SUPABASE_URL, settings.SUPABASE_KEY)

        # Check if already subscribed
        existing = (
            client.table("digest_subscribers")
            .select("id, status")
            .eq("wa_id", wa_id)
            .limit(1)
            .execute()
        )

        if existing.data:
            sub = existing.data[0]
            if sub["status"] == "active":
                return (
                    f"You're already subscribed to the daily digest for *{city}*! ✅\n\n"
                    "You'll get updates every morning at 8am.\n"
                    "Reply *stop digest* anytime to unsubscribe."
                )
            # Reactivate
            client.table("digest_subscribers").update({
                "status": "active",
                "city": city.strip().title(),
                "updated_at": datetime.now(timezone.utc).isoformat(),
            }).eq("id", sub["id"]).execute()
        else:
            # New subscriber
            import uuid
            client.table("digest_subscribers").insert({
                "id": str(uuid.uuid4()),
                "wa_id": wa_id,
                "city": city.strip().title(),
                "status": "active",
            }).execute()

        logger.info(f"Digest subscription: {wa_id} → {city}")
        return (
            f"🎉 You're subscribed to the *{city.title()} Daily Digest*!\n\n"
            "Every morning at 8am you'll get:\n"
            "• Top deals with quick reply actions\n"
            "• New businesses & openings\n"
            "• Featured local businesses\n\n"
            "Reply *stop digest* anytime to unsubscribe."
        )

    except Exception as e:
        logger.error(f"Failed to subscribe {wa_id} to digest: {e}")
        return "Sorry, couldn't set up your digest right now. Try again later. 🙏"


def unsubscribe_from_digest(wa_id: str, settings: Settings) -> str:
    """Opt a user out of the daily digest."""
    try:
        client = create_client(settings.SUPABASE_URL, settings.SUPABASE_KEY)
        result = (
            client.table("digest_subscribers")
            .update({
                "status": "inactive",
                "updated_at": datetime.now(timezone.utc).isoformat(),
            })
            .eq("wa_id", wa_id)
            .eq("status", "active")
            .execute()
        )

        if result.data:
            logger.info(f"Digest unsubscribe: {wa_id}")
            return (
                "You've been unsubscribed from the daily digest. ✅\n\n"
                "You can re-subscribe anytime by typing *daily digest*."
            )
        return (
            "You're not currently subscribed to the digest.\n"
            "Type *daily digest* to subscribe!"
        )

    except Exception as e:
        logger.error(f"Failed to unsubscribe {wa_id}: {e}")
        return "Sorry, couldn't process that right now. Try again later. 🙏"


def detect_digest_intent(message: str) -> str | None:
    """
    Detect digest-related intents.
    Returns "subscribe", "unsubscribe", or None.
    """
    msg = message.lower().strip()

    unsub_phrases = [
        "stop digest", "unsubscribe digest", "cancel digest",
        "no more digest", "stop daily digest",
    ]
    sub_phrases = [
        "daily digest", "subscribe digest", "morning digest",
        "daily update", "daily newsletter", "metro digest",
        "sign up for digest", "get daily updates",
    ]

    for phrase in unsub_phrases:
        if phrase in msg:
            return "unsubscribe"
    for phrase in sub_phrases:
        if phrase in msg:
            return "subscribe"
    return None


# ── Digest Reply Handler ─────────────────────────────────────────

def detect_digest_reply(message: str) -> int | None:
    """
    Detect if user is replying to a digest with a number (1-5).
    Returns the deal index (1-based) or None.
    Also detects "4" as the show-more pagination trigger.
    """
    msg = message.strip()
    if msg in ("1", "2", "3", "4", "5"):
        return int(msg)
    return None


def handle_digest_reply(wa_id: str, deal_index: int, settings: Settings) -> str | None:
    """
    Handle a numbered reply to the digest.
    Looks up the cached digest deals for this user and returns the deal details.
    Also tracks the click event for personalization.

    Guards with a context token: if the digest was sent long ago and the token
    has expired, we fall through to normal flow instead of showing stale data.
    """
    try:
        from app.services.session_store import _get_redis
        r = _get_redis(settings)
        if not r:
            return None

        # ── Context token guard: only respond if digest is still "active" ──
        token_key = f"digest_token:{wa_id}"
        token = r.get(token_key)
        if not token:
            return None  # Token expired → digest is stale, fall through

        # ── Look up cached digest deals for this user ──
        cache_key = f"digest_deals:{wa_id}"
        cached = r.get(cache_key)
        if not cached:
            return None  # No recent digest — let normal flow handle it

        deals = json.loads(cached)

        # ── Show-more pagination (reply "4") ──
        if deal_index == 4:
            return _handle_show_more(wa_id, deals, settings, r)

        if deal_index < 1 or deal_index > min(len(deals), 3):
            return None

        deal = deals[deal_index - 1]

        # Track click event for personalization
        _track_digest_event("digest_click", wa_id, {
            "deal_index": deal_index,
            "deal_title": deal.get("title", ""),
            "business_name": deal.get("business_name", ""),
            "category": deal.get("category", ""),
        }, settings)

        # Build rich detail message
        biz_name = deal.get("business_name", "Local Business")
        title = deal.get("title", "Deal")
        desc = deal.get("description", "")
        city = deal.get("city", "")
        state = deal.get("state", "")
        phone = deal.get("phone", "")

        expire_str = ""
        if deal.get("expires_at"):
            try:
                exp = datetime.fromisoformat(deal["expires_at"].replace("Z", "+00:00"))
                days_left = (exp - datetime.now(timezone.utc)).days
                if days_left <= 0:
                    expire_str = "\n⏰ *Last day to grab this deal!*"
                elif days_left <= 2:
                    expire_str = f"\n⏰ Ends in {days_left} day{'s' if days_left > 1 else ''}"
            except Exception:
                pass

        msg = (
            f"📋 *{title}*\n\n"
            f"🏪 {biz_name}\n"
        )
        if desc:
            msg += f"📝 {desc}\n"
        if city:
            msg += f"📍 {city}, {state}\n"
        msg += expire_str

        # ── Call now action (phone number from business) ──
        if phone:
            msg += f"\n📞 *Call now:* {phone}"

        msg += (
            "\n\n👉 Say *\"browse deals\"* to see all deals\n"
            "👉 Say *\"boost\"* to promote your own deal"
        )

        return msg

    except Exception as e:
        logger.warning(f"Digest reply handling failed for {wa_id}: {e}")
        return None


def _handle_show_more(wa_id: str, deals: list[dict], settings: Settings, r) -> str:
    """
    Handle 'show more' pagination when user replies "4" to a digest.
    Shows deals 4-6 from the cached list.
    """
    # Track current offset (starts at 3 since digest shows 1-3)
    offset_key = f"digest_offset:{wa_id}"
    raw_offset = r.get(offset_key)
    offset = int(raw_offset) if raw_offset else 3

    page = deals[offset:offset + 3]

    if not page:
        return (
            "That's all the deals for now! 🎉\n\n"
            "👉 Say *\"browse deals\"* for the full marketplace\n"
            "👉 Or check back tomorrow for new deals"
        )

    # Build compact page
    msg = "📋 *More deals:*\n\n"
    for i, d in enumerate(page, offset + 1):
        biz_name = d.get("business_name", "Local Business")
        title = d.get("title", "")
        badge = ""
        if d.get("is_boosted"):
            badge = " 🚀"
        msg += f"*{i}.* {title} @ {biz_name}{badge}\n"

    # Update offset for next "show more"
    new_offset = offset + 3
    r.setex(offset_key, 600, str(new_offset))  # 10min TTL

    if new_offset < len(deals):
        msg += "\n👉 Reply *4* for even more deals"
    else:
        msg += "\n✅ That's all current deals!"

    msg += "\n👉 Say *\"browse deals\"* for the full marketplace"

    _track_digest_event("digest_show_more", wa_id, {"offset": offset}, settings)
    return msg


# ── Digest Content Builder ────────────────────────────────────────

def _get_new_businesses(city: str, settings: Settings, days: int = 7) -> list[dict]:
    """Get businesses added in the last N days for this city."""
    try:
        client = create_client(settings.SUPABASE_URL, settings.SUPABASE_KEY)
        since = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
        result = (
            client.table("businesses")
            .select("name, category, city, state")
            .ilike("city", f"%{city}%")
            .gte("created_at", since)
            .order("created_at", desc=True)
            .limit(5)
            .execute()
        )
        return result.data or []
    except Exception as e:
        logger.warning(f"Failed to get new businesses for {city}: {e}")
        return []


def _get_active_deals(city: str, settings: Settings, limit: int = 5) -> list[dict]:
    """
    Get active deals for this city, ranked for digest.
    v2: Boosted deals ALWAYS rank first, then featured, urgency, recency.
    Returns top N deals with full data for caching.
    """
    try:
        client = create_client(settings.SUPABASE_URL, settings.SUPABASE_KEY)
        now = datetime.now(timezone.utc)
        now_iso = now.isoformat()

        # Fetch more than needed for ranking
        result = (
            client.table("deals")
            .select("id, title, description, business_name, business_id, "
                    "category, city, state, expires_at, created_at, boosted_until")
            .ilike("city", f"%{city}%")
            .eq("is_active", True)
            .gte("expires_at", now_iso)
            .order("created_at", desc=True)
            .limit(20)
            .execute()
        )

        deals = result.data or []
        if not deals:
            return []

        # Look up which businesses are featured
        biz_ids = list({d.get("business_id") for d in deals if d.get("business_id")})
        featured_ids: set[str] = set()
        if biz_ids:
            try:
                fr = (
                    client.table("businesses")
                    .select("id")
                    .eq("is_featured", True)
                    .in_("id", biz_ids)
                    .execute()
                )
                featured_ids = {b["id"] for b in (fr.data or [])}
            except Exception:
                pass

        # Score and rank — boosted deals always on top
        def _score(d: dict) -> float:
            s = 0.0

            # Boosted: highest priority (+200) — this is paid placement
            boost_until = d.get("boosted_until", "")
            if boost_until:
                try:
                    boost_dt = datetime.fromisoformat(boost_until.replace("Z", "+00:00"))
                    if boost_dt > now:
                        s += 200
                        d["is_boosted"] = True
                except Exception:
                    pass

            # Featured business
            if d.get("business_id") in featured_ids:
                s += 100
                d["is_featured_business"] = True

            # Urgency: expiring within 48h gets boost
            exp = d.get("expires_at", "")
            if exp:
                try:
                    exp_dt = datetime.fromisoformat(exp.replace("Z", "+00:00"))
                    hours_left = (exp_dt - now).total_seconds() / 3600
                    if 0 < hours_left < 48:
                        s += 50
                except Exception:
                    pass

            # Recency
            cr = d.get("created_at", "")
            if cr:
                try:
                    cr_dt = datetime.fromisoformat(cr.replace("Z", "+00:00"))
                    age_h = (now - cr_dt).total_seconds() / 3600
                    s += max(0, 30 - age_h)
                except Exception:
                    pass

            return s

        deals.sort(key=_score, reverse=True)
        return deals[:limit]

    except Exception as e:
        logger.warning(f"Failed to get deals for {city}: {e}")
        return []


def _get_featured_businesses(city: str, settings: Settings) -> list[dict]:
    """Get featured businesses for sponsored slot, with rotation."""
    try:
        client = create_client(settings.SUPABASE_URL, settings.SUPABASE_KEY)
        result = (
            client.table("businesses")
            .select("name, category, phone, city, state")
            .ilike("city", f"%{city}%")
            .eq("is_featured", True)
            .limit(10)
            .execute()
        )
        businesses = result.data or []
        # Rotate: random selection so different featured businesses appear each day
        if len(businesses) > 2:
            random.shuffle(businesses)
        return businesses[:2]
    except Exception as e:
        logger.warning(f"Failed to get featured businesses for {city}: {e}")
        return []


def _get_total_business_count(city: str, settings: Settings) -> int:
    """Get total business count for this city."""
    try:
        client = create_client(settings.SUPABASE_URL, settings.SUPABASE_KEY)
        result = (
            client.table("businesses")
            .select("id", count="exact")
            .ilike("city", f"%{city}%")
            .execute()
        )
        return result.count if result.count else 0
    except Exception:
        return 0


def _get_user_preferred_category(wa_id: str, settings: Settings) -> str | None:
    """
    Look up the user's most recent search category for personalization.
    Uses inquiry_logs to find what they search for most.
    """
    try:
        client = create_client(settings.SUPABASE_URL, settings.SUPABASE_KEY)
        result = (
            client.table("inquiry_logs")
            .select("query")
            .eq("wa_id", wa_id)
            .order("created_at", desc=True)
            .limit(5)
            .execute()
        )

        if not result.data:
            return None

        # Simple frequency: count category keywords from recent queries
        from app.services.business_service import CATEGORY_MAP
        cat_counts: dict[str, int] = {}
        for row in result.data:
            query = (row.get("query") or "").lower()
            for keyword, category in CATEGORY_MAP.items():
                if keyword in query:
                    cat_counts[category] = cat_counts.get(category, 0) + 1

        if cat_counts:
            return max(cat_counts, key=cat_counts.get)
        return None

    except Exception as e:
        logger.warning(f"Failed to get user preference for {wa_id}: {e}")
        return None


def _personalize_deals(deals: list[dict], preferred_category: str | None) -> list[dict]:
    """
    Reorder deals to show user's preferred category first.
    Preserves boosted deals at top (they already have highest scores).
    """
    if not preferred_category or len(deals) <= 1:
        return deals

    # Split into boosted (stay at top) and non-boosted (reorderable)
    boosted = [d for d in deals if d.get("is_boosted")]
    non_boosted = [d for d in deals if not d.get("is_boosted")]

    # Move preferred category to front of non-boosted
    preferred = [d for d in non_boosted if (d.get("category") or "").lower() == preferred_category]
    others = [d for d in non_boosted if (d.get("category") or "").lower() != preferred_category]

    return boosted + preferred + others


# ── Message Assembly ─────────────────────────────────────────────

def build_digest_message(
    city: str,
    settings: Settings,
    wa_id: str | None = None,
) -> tuple[str, list[dict]]:
    """
    Build the daily digest message for a city.
    Returns (message_text, deals_list) — deals_list is cached for reply handling.

    v2.1 tweaks:
    - Freshness timestamp line at top
    - Boosted slots capped at 2
    - "Show more" CTA (reply 4)
    - Fetches more deals (10) so pagination has content
    """
    now = datetime.now(timezone.utc)
    # EST for user-facing timestamp
    est_offset = timedelta(hours=-5)
    est_now = now + est_offset

    new_biz = _get_new_businesses(city, settings)
    deals = _get_active_deals(city, settings, limit=10)  # Fetch more for pagination
    featured = _get_featured_businesses(city, settings)
    total_count = _get_total_business_count(city, settings)

    # ── Cap boosted slots at 2 (Tweak 4) ──
    boosted_count = 0
    for d in deals:
        if d.get("is_boosted"):
            boosted_count += 1
            if boosted_count > 2:
                d["is_boosted"] = False  # Demote to normal ranking

    # Personalize deal order if we know the user
    if wa_id:
        pref = _get_user_preferred_category(wa_id, settings)
        if pref:
            deals = _personalize_deals(deals, pref)

    # ── Freshness line (Tweak 5) ──
    time_str = est_now.strftime("%-I:%M %p")
    msg = f"📍 *{city}* • Updated today at {time_str}\n\n"

    # ── Header — action-driven ──
    msg += f"🔥 *Top deals in {city} TODAY*\n\n"

    # ── Deals — numbered with quick reply (show top 3) ──
    if deals:
        for i, d in enumerate(deals[:3], 1):
            biz_name = d.get("business_name", "Local Business")
            title = d.get("title", "")

            # Badge line
            badge = ""
            if d.get("is_boosted"):
                badge = " 🚀 *Boosted*"
            elif d.get("is_featured_business"):
                badge = " ⭐ Featured"

            # Urgency
            urgency = ""
            if d.get("expires_at"):
                try:
                    exp = datetime.fromisoformat(d["expires_at"].replace("Z", "+00:00"))
                    days_left = (exp - now).days
                    hours_left = (exp - now).total_seconds() / 3600
                    if hours_left <= 24:
                        urgency = "\n   ⏰ *Ends today!*"
                    elif days_left <= 2:
                        urgency = "\n   ⏰ Ends soon"
                except Exception:
                    pass

            # Freshness badge for new deals
            freshness = ""
            if d.get("created_at"):
                try:
                    cr = datetime.fromisoformat(d["created_at"].replace("Z", "+00:00"))
                    if (now - cr).days <= 1:
                        freshness = "🆕 "
                except Exception:
                    pass

            msg += (
                f"*{i}.* {freshness}{title} @ {biz_name}{badge}"
                f"{urgency}\n"
                f"   👉 Reply *\"{i}\"* for details\n\n"
            )

        # ── Show more CTA (Tweak 2) — only if more deals exist ──
        if len(deals) > 3:
            msg += "👉 Reply *\"4\"* to see more deals\n\n"
    else:
        msg += "No new deals today — check back tomorrow!\n\n"

    # ── New Businesses (compact) ──
    if new_biz:
        names = ", ".join(b["name"] for b in new_biz[:3])
        extra = f" +{len(new_biz) - 3} more" if len(new_biz) > 3 else ""
        msg += f"🏪 *New this week:* {names}{extra}\n\n"

    # ── Sponsored Slot (rotated featured business) ──
    if featured:
        f_biz = featured[0]
        phone = f_biz.get("phone", "")
        cat = f_biz.get("category", "")
        phone_str = f" — {phone}" if phone else ""
        cat_str = f" ({cat})" if cat else ""
        msg += f"⭐ *Sponsored:* {f_biz['name']}{cat_str}{phone_str}\n\n"

    # ── Footer — interactive ──
    msg += "👉 Reply *more* for all deals\n"
    msg += "👉 Reply *post deal* to promote yours\n"
    msg += "_Reply *stop digest* to unsubscribe_"

    return msg, deals


def build_expiring_deals_message(city: str, settings: Settings) -> str | None:
    """
    Build the evening "expiring deals" push for a city.
    Only sent if there are deals expiring within the next 18 hours.
    Returns None if no expiring deals.
    """
    now = datetime.now(timezone.utc)
    deadline = (now + timedelta(hours=18)).isoformat()

    try:
        client = create_client(settings.SUPABASE_URL, settings.SUPABASE_KEY)
        result = (
            client.table("deals")
            .select("title, business_name, expires_at")
            .ilike("city", f"%{city}%")
            .eq("is_active", True)
            .gte("expires_at", now.isoformat())
            .lte("expires_at", deadline)
            .order("expires_at", desc=False)
            .limit(5)
            .execute()
        )

        deals = result.data or []
        if not deals:
            return None

        msg = f"⏰ *Deals ending soon in {city}!*\n\n"
        for d in deals[:3]:
            biz = d.get("business_name", "Local Business")
            title = d.get("title", "Deal")
            msg += f"• {title} @ {biz}\n"

        msg += (
            "\n👉 Reply *browse deals* to see all\n"
            "👉 Reply *boost* to promote your deal to the top"
        )

        return msg

    except Exception as e:
        logger.warning(f"Failed to build expiring deals for {city}: {e}")
        return None


# ── Business Phone Lookup ──────────────────────────────────────────

def _get_business_phones(business_ids: list[str], settings: Settings) -> dict[str, str]:
    """Batch-fetch phone numbers for a list of business IDs."""
    if not business_ids:
        return {}
    try:
        client = create_client(settings.SUPABASE_URL, settings.SUPABASE_KEY)
        unique_ids = list(set(business_ids))
        result = (
            client.table("businesses")
            .select("id, phone")
            .in_("id", unique_ids)
            .execute()
        )
        return {b["id"]: b.get("phone", "") for b in (result.data or []) if b.get("phone")}
    except Exception as e:
        logger.warning(f"Failed to fetch business phones: {e}")
        return {}


# ── Tracking ─────────────────────────────────────────────────────

def _track_digest_event(
    event: str, wa_id: str, details: dict, settings: Settings,
) -> None:
    """Log digest interaction events for personalization + analytics."""
    try:
        client = create_client(settings.SUPABASE_URL, settings.SUPABASE_KEY)
        client.table("notification_log").insert({
            "business_id": details.get("business_id", ""),
            "business_name": details.get("business_name", ""),
            "owner_wa_id": wa_id,
            "search_query": event,
            "status": "digest_event",
            "details": json.dumps(details),
        }).execute()
    except Exception as e:
        logger.warning(f"Digest event tracking failed: {e}")


# ── Main Send Functions ──────────────────────────────────────────

async def send_daily_digest(settings: Settings) -> dict:
    """
    Send daily digest to all active subscribers.
    Called by cron at 8am EST.
    v2: Personalized per user, with cached deal lists for reply handling.
    """
    from app.services.whatsapp_service import WhatsAppService
    from app.services.session_store import _get_redis

    try:
        client = create_client(settings.SUPABASE_URL, settings.SUPABASE_KEY)
        subscribers = (
            client.table("digest_subscribers")
            .select("wa_id, city")
            .eq("status", "active")
            .execute()
        )
    except Exception as e:
        logger.error(f"Failed to fetch digest subscribers: {e}")
        return {"error": str(e), "sent": 0}

    if not subscribers.data:
        logger.info("No active digest subscribers found")
        return {"sent": 0, "total_subscribers": 0}

    whatsapp = WhatsAppService(settings)
    r = _get_redis(settings)
    sent = 0
    failed = 0

    # Cache base digest per city (non-personalized), then personalize per user
    city_base_deals: dict[str, list[dict]] = {}

    for sub in subscribers.data:
        city = sub.get("city", "").strip().title()
        wa_id = sub.get("wa_id", "")

        if not city or not wa_id:
            continue

        try:
            # Build personalized digest for this user
            msg, deals = build_digest_message(city, settings, wa_id=wa_id)

            # Cache deal list in Redis so reply handler can look them up
            if r and deals:
                try:
                    # Look up phone numbers for each deal's business
                    biz_phones = _get_business_phones(
                        [d.get("business_id") for d in deals if d.get("business_id")],
                        settings,
                    )
                    cache_data = json.dumps([{
                        "title": d.get("title", ""),
                        "description": d.get("description", ""),
                        "business_name": d.get("business_name", ""),
                        "category": d.get("category", ""),
                        "city": d.get("city", ""),
                        "state": d.get("state", ""),
                        "expires_at": d.get("expires_at", ""),
                        "is_boosted": d.get("is_boosted", False),
                        "phone": biz_phones.get(d.get("business_id", ""), ""),
                    } for d in deals])
                    r.setex(f"digest_deals:{wa_id}", 86400, cache_data)  # 24h TTL

                    # Set context token (12h TTL) — guards numbered replies
                    import secrets
                    r.setex(f"digest_token:{wa_id}", 43200, secrets.token_hex(4))
                except Exception:
                    pass

            await whatsapp.send_text_message(wa_id, msg)
            sent += 1

            # Track send event
            _track_digest_event("digest_sent", wa_id, {"city": city}, settings)

        except Exception as e:
            logger.error(f"Failed to send digest to {wa_id}: {e}")
            failed += 1

    summary = {
        "total_subscribers": len(subscribers.data),
        "sent": sent,
        "failed": failed,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }

    logger.info(f"Daily digest complete: {summary}")
    return summary


async def send_evening_expiring_deals(settings: Settings) -> dict:
    """
    Send evening "expiring deals" push to all active subscribers.
    Called by cron at 5pm EST.
    Only sends if there are deals expiring within 18 hours.
    """
    from app.services.whatsapp_service import WhatsAppService

    try:
        client = create_client(settings.SUPABASE_URL, settings.SUPABASE_KEY)
        subscribers = (
            client.table("digest_subscribers")
            .select("wa_id, city")
            .eq("status", "active")
            .execute()
        )
    except Exception as e:
        logger.error(f"Failed to fetch digest subscribers: {e}")
        return {"error": str(e), "sent": 0}

    if not subscribers.data:
        return {"sent": 0, "total_subscribers": 0}

    whatsapp = WhatsAppService(settings)
    sent = 0
    skipped = 0

    # Cache per city
    city_messages: dict[str, str | None] = {}

    for sub in subscribers.data:
        city = sub.get("city", "").strip().title()
        wa_id = sub.get("wa_id", "")

        if not city or not wa_id:
            continue

        if city not in city_messages:
            city_messages[city] = build_expiring_deals_message(city, settings)

        msg = city_messages[city]
        if not msg:
            skipped += 1
            continue

        try:
            await whatsapp.send_text_message(wa_id, msg)
            sent += 1
        except Exception as e:
            logger.error(f"Failed to send evening push to {wa_id}: {e}")

    summary = {
        "total_subscribers": len(subscribers.data),
        "sent": sent,
        "skipped_no_expiring": skipped,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }

    logger.info(f"Evening expiring deals push complete: {summary}")
    return summary
