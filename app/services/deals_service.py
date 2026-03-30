"""
Mira — Deals & Promotions Service

Handles:
  1. Business owners posting deals via WhatsApp conversation
  2. Users searching/browsing deals by city, category, or keyword
  3. Auto-expiry of old deals

Uses Redis-backed session store (survives container restarts).
"""

import logging
import time
import uuid
from datetime import datetime, timedelta, timezone
from enum import Enum

from supabase import create_client
from config.settings import Settings
from app.services.session_store import get_session, set_session, delete_session, session_exists

logger = logging.getLogger(__name__)

# Redis key prefix for deal sessions
_KEY_PREFIX = "deal:"

# ── Deal types ──────────────────────────────────────────────────
DEAL_TYPES = ["discount", "bogo", "freebie", "special", "coupon", "event"]

DEAL_TYPE_DISPLAY = {
    "discount": "💰 Discount / % Off",
    "bogo": "🎁 Buy One Get One",
    "freebie": "🆓 Free Item / Sample",
    "special": "⭐ Daily / Weekly Special",
    "coupon": "🎟️ Coupon Code",
    "event": "🎉 Event / Grand Opening",
}

DURATION_OPTIONS = {
    "1": ("1 day", timedelta(days=1)),
    "2": ("3 days", timedelta(days=3)),
    "3": ("1 week", timedelta(days=7)),
    "4": ("2 weeks", timedelta(days=14)),
    "5": ("1 month", timedelta(days=30)),
}


# ── Flow steps ──────────────────────────────────────────────────
class DealStep(str, Enum):
    BUSINESS_LOOKUP = "business_lookup"
    SELECT_BUSINESS = "select_business"
    TITLE = "title"
    DESCRIPTION = "description"
    DEAL_TYPE = "deal_type"
    DURATION = "duration"
    CONFIRM = "confirm"


def _key(wa_id: str) -> str:
    return f"{_KEY_PREFIX}{wa_id}"


def _get(wa_id: str, settings: Settings) -> dict | None:
    return get_session(_key(wa_id), settings)


def _save(wa_id: str, session: dict, settings: Settings) -> None:
    set_session(_key(wa_id), session, settings)


def _del(wa_id: str, settings: Settings) -> None:
    delete_session(_key(wa_id), settings)


def has_active_deal_session(wa_id: str, settings: Settings | None = None) -> bool:
    if settings is None:
        from config.settings import get_settings
        settings = get_settings()
    return session_exists(_key(wa_id), settings)


def cancel_deal_session(wa_id: str, settings: Settings | None = None) -> str:
    if settings is None:
        from config.settings import get_settings
        settings = get_settings()
    _del(wa_id, settings)
    return "Deal posting cancelled. How else can I help? 🙏"


# ── Intent detection ────────────────────────────────────────────
def detect_deal_intent(message: str) -> str | None:
    """
    Detect if user wants to post a deal or browse deals.
    Returns "post", "browse", "browse_today", or None.
    """
    msg = message.lower().strip()

    post_phrases = [
        "post a deal", "post deal", "add a deal", "add deal",
        "create a deal", "new deal", "post promotion", "add promotion",
        "post a promotion", "post my deal", "create promotion",
        "post an offer", "add an offer", "post offer",
        "i have a deal", "i have a promotion", "share a deal",
    ]

    # Today-specific browse phrases (checked first — more specific)
    today_phrases = [
        "today's deals", "todays deals", "deals today",
        "today's offers", "todays offers", "offers today",
        "today's discounts", "todays discounts", "discounts today",
        "today's coupons", "todays coupons", "coupons today",
        "what's on today", "whats on today",
    ]

    browse_phrases = [
        # Core deal terms
        "deals near", "any deals", "show deals", "find deals",
        "deals in", "deals around", "browse deals", "latest deals",
        "what deals", "show me deals", "current deals", "list deals",
        "list all deals", "show all deals",
        # Discount synonyms
        "discounts near", "any discounts", "show discounts", "find discounts",
        "discounts in", "discounts around",
        # Offer synonyms
        "offers near", "any offers", "show offers", "find offers",
        "offers in", "offers around",
        # Coupon synonyms
        "coupons near", "any coupons", "show coupons", "find coupons",
        "coupons in", "coupons around",
        # Promotion synonyms
        "promotions near", "any promotions", "show promotions",
        "promotions in", "promos near", "any promos",
        # Sale synonyms
        "sales near", "any sales", "show sales", "sales in",
        # Generic
        "what's on sale", "whats on sale",
    ]

    for phrase in post_phrases:
        if phrase in msg:
            return "post"
    for phrase in today_phrases:
        if phrase in msg:
            return "browse_today"
    for phrase in browse_phrases:
        if phrase in msg:
            return "browse"
    return None


def _extract_category_filter(message: str) -> str | None:
    """Extract category keyword from deal search query for filtering."""
    msg = message.lower()
    category_map = {
        "grocery": "grocery",
        "groceries": "grocery",
        "food": "restaurant",
        "restaurant": "restaurant",
        "tiffin": "restaurant",
        "catering": "restaurant",
        "beauty": "beauty",
        "salon": "beauty",
        "clothing": "clothing",
        "jewelry": "jewelry",
        "temple": "religious",
        "tutoring": "education",
        "education": "education",
        "real estate": "real estate",
        "insurance": "insurance",
        "tax": "tax",
        "immigration": "immigration",
        "lawyer": "legal",
        "doctor": "healthcare",
        "dentist": "healthcare",
        "pharmacy": "healthcare",
    }
    for keyword, category in category_map.items():
        if keyword in msg:
            return category
    return None


# ── Start deal posting flow ─────────────────────────────────────
def start_deal_flow(wa_id: str, settings: Settings | None = None) -> str:
    """Begin the 'post a deal' conversation."""
    if settings is None:
        from config.settings import get_settings
        settings = get_settings()
    _save(wa_id, {
        "wa_id": wa_id,
        "step": DealStep.BUSINESS_LOOKUP,
        "data": {},
        "matches": [],
    }, settings)
    return (
        "Let's post a deal for your business! 🎉\n\n"
        "First, tell me your *business name* or *phone number* "
        "so I can link this deal to your listing."
    )


# ── Menu helpers ────────────────────────────────────────────────
def _deal_type_menu() -> str:
    lines = ["What type of deal is this? (reply with the number):\n"]
    for i, dt in enumerate(DEAL_TYPES, 1):
        lines.append(f"{i}. {DEAL_TYPE_DISPLAY[dt]}")
    return "\n".join(lines)


def _parse_deal_type(msg: str) -> str | None:
    msg = msg.strip().lower()
    try:
        idx = int(msg)
        if 1 <= idx <= len(DEAL_TYPES):
            return DEAL_TYPES[idx - 1]
    except ValueError:
        pass
    for dt in DEAL_TYPES:
        if dt in msg:
            return dt
    return None


def _duration_menu() -> str:
    lines = ["How long should this deal run? (reply with the number):\n"]
    for k, (label, _) in DURATION_OPTIONS.items():
        lines.append(f"{k}. {label}")
    return "\n".join(lines)


def _parse_duration(msg: str) -> tuple[str, timedelta] | None:
    msg = msg.strip()
    opt = DURATION_OPTIONS.get(msg)
    if opt:
        return opt
    return None


# ── Main handler ────────────────────────────────────────────────
def handle_deal_message(wa_id: str, message: str, settings: Settings) -> str:
    """Process a message within an active deal-posting session."""
    session = _get(wa_id, settings)
    if not session:
        return "No active deal session. Say *'post a deal'* to start."

    msg = message.strip()
    if msg.lower() in ("cancel", "stop", "quit", "exit", "nevermind"):
        return cancel_deal_session(wa_id, settings)

    return _handle_deal_step(session, msg, settings)


# ── Deal posting step handler ───────────────────────────────────
def _handle_deal_step(session: dict, msg: str, settings: Settings) -> str:
    step = session["step"]
    wa_id = session["wa_id"]
    data = session.get("data", {})

    if step == DealStep.BUSINESS_LOOKUP:
        return _deal_lookup_business(session, msg, settings)

    elif step == DealStep.SELECT_BUSINESS:
        return _deal_select_business(session, msg, settings)

    elif step == DealStep.TITLE:
        data["title"] = msg
        session["data"] = data
        session["step"] = DealStep.DESCRIPTION
        _save(wa_id, session, settings)
        return (
            f"Deal title: *{msg}*\n\n"
            "Now give me a short *description* of the deal.\n"
            "(e.g. \"20% off all dosas this weekend\" or \"Free chai with any lunch combo\")"
        )

    elif step == DealStep.DESCRIPTION:
        data["description"] = msg
        session["data"] = data
        session["step"] = DealStep.DEAL_TYPE
        _save(wa_id, session, settings)
        return f"Got it!\n\n{_deal_type_menu()}"

    elif step == DealStep.DEAL_TYPE:
        dt = _parse_deal_type(msg)
        if not dt:
            return f"I didn't catch that.\n\n{_deal_type_menu()}"
        data["deal_type"] = dt
        session["data"] = data
        session["step"] = DealStep.DURATION
        _save(wa_id, session, settings)
        return f"Type: *{DEAL_TYPE_DISPLAY[dt]}*\n\n{_duration_menu()}"

    elif step == DealStep.DURATION:
        result = _parse_duration(msg)
        if not result:
            return f"Please pick a number.\n\n{_duration_menu()}"
        label, delta = result
        data["duration_label"] = label
        # Store timedelta as seconds (JSON-serializable)
        data["duration_seconds"] = int(delta.total_seconds())
        session["data"] = data
        session["step"] = DealStep.CONFIRM
        _save(wa_id, session, settings)
        return _deal_confirmation(data)

    elif step == DealStep.CONFIRM:
        if msg.lower() in ("yes", "y", "confirm", "looks good", "correct", "ok", "👍"):
            return _insert_deal(session, settings)
        elif msg.lower() in ("no", "n", "restart", "start over"):
            session["step"] = DealStep.TITLE
            session["data"] = {k: v for k, v in data.items() if k in ("business",)}
            _save(wa_id, session, settings)
            return "No problem! Let's start over.\n\nWhat's the *deal title*? (e.g. \"Weekend Dosa Fest\")"
        else:
            return "Please reply *yes* to post or *no* to start over."

    return "Something went wrong. Type 'cancel' to exit."


# ── Business lookup for deal posting ────────────────────────────
def _deal_lookup_business(session: dict, msg: str, settings: Settings) -> str:
    """Find the business this deal belongs to."""
    wa_id = session["wa_id"]
    data = session.get("data", {})
    try:
        client = create_client(settings.SUPABASE_URL, settings.SUPABASE_KEY)
        digits = "".join(c for c in msg if c.isdigit())
        if len(digits) >= 7:
            result = client.table("businesses").select("*").ilike("phone", f"%{digits[-10:]}%").execute()
        else:
            result = client.table("businesses").select("*").ilike("name", f"%{msg}%").execute()

        if not result.data:
            return (
                "I couldn't find that business in our directory.\n"
                "Please check the name/phone, or *add your business first* by typing 'add my business'.\n\n"
                "Type *cancel* to exit."
            )

        if len(result.data) == 1:
            data["business"] = result.data[0]
            session["data"] = data
            session["step"] = DealStep.TITLE
            _save(wa_id, session, settings)
            b = result.data[0]
            return (
                f"Found: *{b['name']}* — {b.get('city', '')}, {b.get('state', '')}\n\n"
                "What's the *deal title*?\n"
                "(e.g. \"Weekend Dosa Fest\" or \"Grand Opening — 30% Off\")"
            )

        session["matches"] = result.data[:5]
        session["step"] = DealStep.SELECT_BUSINESS
        _save(wa_id, session, settings)
        lines = ["Multiple matches found. Which one? (reply with the number)\n"]
        for i, b in enumerate(result.data[:5], 1):
            lines.append(f"{i}. *{b['name']}* — {b.get('city', '')}, {b.get('state', '')}")
        return "\n".join(lines)

    except Exception as e:
        logger.error(f"Deal business lookup failed for {wa_id}: {e}")
        return "Sorry, something went wrong. Please try again. 🙏"


def _deal_select_business(session: dict, msg: str, settings: Settings) -> str:
    wa_id = session["wa_id"]
    matches = session.get("matches", [])
    data = session.get("data", {})
    try:
        idx = int(msg.strip()) - 1
        if 0 <= idx < len(matches):
            data["business"] = matches[idx]
            session["data"] = data
            session["step"] = DealStep.TITLE
            _save(wa_id, session, settings)
            b = matches[idx]
            return (
                f"Selected: *{b['name']}*\n\n"
                "What's the *deal title*?\n"
                "(e.g. \"Weekend Dosa Fest\" or \"Grand Opening — 30% Off\")"
            )
    except (ValueError, IndexError):
        pass
    return "Please reply with a number from the list, or type *cancel* to exit."


# ── Confirmation & insert ───────────────────────────────────────
def _deal_confirmation(data: dict) -> str:
    b = data["business"]
    return (
        "Here's your deal:\n\n"
        f"🏪 *{b['name']}*\n"
        f"🏷️ *{data['title']}*\n"
        f"📝 {data['description']}\n"
        f"📂 {DEAL_TYPE_DISPLAY.get(data['deal_type'], data['deal_type'])}\n"
        f"⏰ Runs for {data['duration_label']}\n\n"
        "Reply *yes* to post or *no* to start over."
    )


def _check_deal_limit(business_id: str, wa_id: str, settings: Settings) -> str | None:
    """
    Check if business has exceeded monthly deal limit based on subscription plan.
    Returns None if within limits, or an error message string if exceeded.
    """
    try:
        client = create_client(settings.SUPABASE_URL, settings.SUPABASE_KEY)

        # Get current plan
        sub = (
            client.table("subscriptions")
            .select("plan")
            .eq("business_id", business_id)
            .eq("status", "active")
            .limit(1)
            .execute()
        )
        plan = sub.data[0]["plan"] if sub.data else "free"

        # Plan limits
        limits = {"free": 1, "featured": 5, "premium": 999}
        max_deals = limits.get(plan, 1)

        # Count deals posted this month
        month_start = datetime.now(timezone.utc).replace(day=1, hour=0, minute=0, second=0).isoformat()
        count = (
            client.table("deals")
            .select("id", count="exact")
            .eq("business_id", business_id)
            .gte("created_at", month_start)
            .execute()
        )
        current = count.count if count.count else 0

        if current >= max_deals:
            plan_label = {"free": "Free", "featured": "Featured", "premium": "Premium"}.get(plan, "Free")
            if plan == "free":
                return (
                    "You've used your 1 free deal this month 👍\n\n"
                    "Upgrade to post more and reach more customers:\n\n"
                    "⭐ *Featured* – $15/month (5 deals)\n"
                    "👑 *Premium* – $30/month (unlimited)\n\n"
                    "Reply *\"upgrade\"* to continue 👉"
                )
            return (
                f"You've used all *{max_deals} deals/month* on the {plan_label} plan.\n\n"
                "Need more? Reply *\"upgrade\"* to see options."
            )
        return None

    except Exception as e:
        logger.warning(f"Deal limit check failed: {e}")
        return None  # On failure, allow the deal


def _insert_deal(session: dict, settings: Settings) -> str:
    """Insert the deal into Supabase."""
    data = session["data"]
    wa_id = session["wa_id"]
    b = data["business"]
    now = datetime.now(timezone.utc)
    duration_seconds = data.get("duration_seconds", 86400)  # default 1 day
    expires = now + timedelta(seconds=duration_seconds)

    # ── Enforce deal limits ──
    limit_msg = _check_deal_limit(b["id"], wa_id, settings)
    if limit_msg:
        _del(wa_id, settings)
        return limit_msg

    try:
        client = create_client(settings.SUPABASE_URL, settings.SUPABASE_KEY)
        row = {
            "id": str(uuid.uuid4()),
            "business_id": b["id"],
            "business_name": b["name"],
            "title": data["title"],
            "description": data["description"],
            "deal_type": data["deal_type"],
            "category": b.get("category"),
            "city": b.get("city"),
            "state": b.get("state"),
            "starts_at": now.isoformat(),
            "expires_at": expires.isoformat(),
            "is_active": True,
            "posted_by_wa_id": wa_id,
        }
        client.table("deals").insert(row).execute()
        _del(wa_id, settings)
        logger.info(f"Deal posted by {wa_id}: {data['title']} for {b['name']}")

        # Check if business is featured for upgrade nudge
        is_featured = b.get("is_featured", False)
        upgrade_nudge = ""
        if not is_featured:
            upgrade_nudge = (
                "\n💡 Want more people to see this?\n"
                "👉 Featured businesses appear first + in the daily digest\n"
                "Reply *\"upgrade\"* to activate"
            )

        return (
            f"Your deal is live! 🎉\n\n"
            f"*{data['title']}* — {b['name']}\n"
            f"Expires in {data['duration_label']}\n\n"
            "People in your area will see this 👍"
            f"{upgrade_nudge}\n\n"
            "Want to post another? Say *\"post a deal\"*"
        )
    except Exception as e:
        logger.error(f"Failed to insert deal for {wa_id}: {e}")
        _del(wa_id, settings)
        return "Sorry, something went wrong while posting your deal. Please try again later. 🙏"


# ── Deal search / browse (for users) ───────────────────────────
def search_deals(
    message: str,
    settings: Settings,
    limit: int = 5,
    today_only: bool = False,
) -> list[dict]:
    """
    Search active deals by city, state, category, or keyword.
    Supports today-only filtering and category extraction.
    Results are ranked: featured businesses first, then by recency.
    Returns a list of deal dicts.
    """
    try:
        client = create_client(settings.SUPABASE_URL, settings.SUPABASE_KEY)
        now = datetime.now(timezone.utc)
        now_iso = now.isoformat()

        query = (
            client.table("deals")
            .select("*")
            .eq("is_active", True)
            .gte("expires_at", now_iso)
            .order("created_at", desc=True)
            .limit(limit * 3)  # fetch more for ranking
        )

        # City/state filter
        from app.services.business_service import detect_city_state
        city, state = detect_city_state(message)
        if city:
            query = query.ilike("city", f"%{city}%")
        if state:
            query = query.eq("state", state.upper())

        # Category filter
        cat_filter = _extract_category_filter(message)
        if cat_filter:
            query = query.ilike("category", f"%{cat_filter}%")

        # Today-only: deals expiring within 24 hours
        if today_only:
            end_of_today = (now + timedelta(hours=24)).isoformat()
            query = query.lte("expires_at", end_of_today)

        result = query.execute()
        deals = result.data if result.data else []

        if not deals:
            return []

        # ── Ranking: featured businesses get boost ──
        # Look up which businesses are featured
        biz_ids = list({d.get("business_id") for d in deals if d.get("business_id")})
        featured_ids: set[str] = set()
        if biz_ids:
            try:
                featured_result = (
                    client.table("businesses")
                    .select("id")
                    .eq("is_featured", True)
                    .in_("id", biz_ids)
                    .execute()
                )
                featured_ids = {b["id"] for b in (featured_result.data or [])}
            except Exception:
                pass

        # Score each deal
        def _deal_score(deal: dict) -> float:
            score = 0.0
            # Featured boost (+100)
            if deal.get("business_id") in featured_ids:
                score += 100
                deal["is_featured_business"] = True
            else:
                deal["is_featured_business"] = False
            # Recency boost (newer = higher, max +50)
            created = deal.get("created_at", "")
            if created:
                try:
                    cr_dt = datetime.fromisoformat(created.replace("Z", "+00:00"))
                    age_hours = (now - cr_dt).total_seconds() / 3600
                    score += max(0, 50 - age_hours)
                except Exception:
                    pass
            # Urgency boost (expiring soon = higher, max +30)
            expires = deal.get("expires_at", "")
            if expires:
                try:
                    exp_dt = datetime.fromisoformat(expires.replace("Z", "+00:00"))
                    hours_left = (exp_dt - now).total_seconds() / 3600
                    if 0 < hours_left < 48:
                        score += 30
                    elif hours_left < 168:  # within a week
                        score += 15
                except Exception:
                    pass
            return score

        deals.sort(key=_deal_score, reverse=True)
        return deals[:limit]

    except Exception as e:
        logger.error(f"Deal search failed: {e}")
        return []


def format_deals_for_prompt(deals: list[dict]) -> str:
    """Format deals for inclusion in Claude's system prompt context."""
    if not deals:
        return ""
    lines = ["\n\n--- Active Deals & Promotions ---"]
    for d in deals:
        expires = d.get("expires_at", "")
        if expires:
            try:
                exp_dt = datetime.fromisoformat(expires.replace("Z", "+00:00"))
                days_left = (exp_dt - datetime.now(timezone.utc)).days
                expire_str = f"{days_left}d left" if days_left > 0 else "expires today"
            except Exception:
                expire_str = ""
        else:
            expire_str = ""
        expire_suffix = f" | ⏰ {expire_str}" if expire_str else ""
        lines.append(
            f"🏷️ {d['title']} — {d['business_name']}\n"
            f"   📝 {d['description']}\n"
            f"   📍 {d.get('city', '')}, {d.get('state', '')}"
            f"{expire_suffix}"
        )
    return "\n".join(lines)


def format_deals_for_whatsapp(deals: list[dict], query_type: str = "all") -> str:
    """Format deals as a WhatsApp-friendly message for browsing."""
    if not deals:
        if query_type == "today":
            return "No deals expiring today. Check *'show deals'* for everything active! 🙏"
        return "No active deals found in that area right now. Check back soon! 🙏"

    header = "🔥 *Today's Deals*\n" if query_type == "today" else "🔥 *Active Deals & Promotions* 🔥\n"
    lines = [header]
    for i, d in enumerate(deals, 1):
        expires = d.get("expires_at", "")
        expire_str = ""
        freshness = ""
        if expires:
            try:
                exp_dt = datetime.fromisoformat(expires.replace("Z", "+00:00"))
                days_left = (exp_dt - datetime.now(timezone.utc)).days
                if days_left <= 0:
                    expire_str = "⏰ Expires today!"
                elif days_left == 1:
                    expire_str = "⏰ 1 day left"
                elif days_left <= 3:
                    expire_str = f"⏰ {days_left} days left"
                else:
                    expire_str = f"⏰ {days_left} days left"
            except Exception:
                pass
        # Freshness badge
        created = d.get("created_at", "")
        if created:
            try:
                cr_dt = datetime.fromisoformat(created.replace("Z", "+00:00"))
                age_days = (datetime.now(timezone.utc) - cr_dt).days
                if age_days <= 1:
                    freshness = "🆕 "
                elif age_days <= 7:
                    freshness = "🔥 "
            except Exception:
                pass
        expire_line = f"\n   {expire_str}" if expire_str else ""
        featured = " ⭐" if d.get("is_featured_business") else ""
        lines.append(
            f"*{i}. {freshness}{d['title']}*{featured}\n"
            f"   🏪 {d['business_name']}\n"
            f"   📝 {d['description']}\n"
            f"   📍 {d.get('city', '')}, {d.get('state', '')}"
            f"{expire_line}\n"
        )
    lines.append("Want to post your own deal? Say *'post a deal'*!")
    return "\n".join(lines)


# ══════════════════════════════════════════════════════════════════
# AUTO-EXPIRY + RE-ENGAGEMENT
# ══════════════════════════════════════════════════════════════════

async def expire_stale_deals(settings: Settings) -> dict:
    """
    Mark expired deals as inactive and notify business owners.
    Called daily by APScheduler cron.
    """
    from app.services.whatsapp_service import WhatsAppService

    try:
        client = create_client(settings.SUPABASE_URL, settings.SUPABASE_KEY)
        now = datetime.now(timezone.utc).isoformat()

        # Find expired deals that are still active
        expired = (
            client.table("deals")
            .select("id, title, business_name, business_id, posted_by_wa_id")
            .eq("is_active", True)
            .lt("expires_at", now)
            .execute()
        )

        if not expired.data:
            return {"expired": 0, "notified": 0}

        # Batch deactivate
        expired_ids = [d["id"] for d in expired.data]
        for deal_id in expired_ids:
            client.table("deals").update({"is_active": False}).eq("id", deal_id).execute()

        # Notify business owners (re-engagement)
        whatsapp = WhatsAppService(settings)
        notified = 0
        seen_owners: set[str] = set()

        for d in expired.data:
            owner_wa = d.get("posted_by_wa_id", "")
            if not owner_wa or owner_wa in seen_owners:
                continue
            seen_owners.add(owner_wa)

            msg = (
                f"⏰ Your deal *{d['title']}* for *{d['business_name']}* has expired.\n\n"
                "Want to keep the momentum going?\n"
                "👉 Reply *\"post a deal\"* to create a new one\n"
                "👉 Reply *\"my stats\"* to see your performance"
            )
            try:
                await whatsapp.send_text_message(owner_wa, msg)
                notified += 1
            except Exception as e:
                logger.warning(f"Failed to notify {owner_wa} about expired deal: {e}")

        summary = {"expired": len(expired_ids), "notified": notified}
        logger.info(f"Deal expiry run: {summary}")
        return summary

    except Exception as e:
        logger.error(f"Deal expiry cron failed: {e}")
        return {"error": str(e)}
