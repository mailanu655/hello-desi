"""
Mira — Deals & Promotions Service (v2 — hardened)

Handles:
  1. Business owners posting deals via WhatsApp conversation
  2. Users searching/browsing deals by city, category, or keyword
  3. Auto-expiry of old deals

v2 improvements:
- Ownership verification (only business owner can post deals)
- Deal limit checked at START of flow (not after 5 steps)
- Category filter aligned with business_service.CATEGORY_MAP
- Session preserved on transient DB errors
- Title/description validation (length limits)
- "back" command to return to previous step
- Duplicate deal detection (title prefix + 7 days)
- Expanded confirmation vocabulary (haan, ji, sure)
- Smooth urgency curve in ranking (replaces hard thresholds)
- Better no-results fallback messages

Uses Redis-backed session store (survives container restarts).
"""

import json
import logging
import re
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

# ── Search cache (reduces DB load on popular queries) ──────────
_SEARCH_CACHE_TTL = 180  # 3 minutes

# ── Deal types ──────────────────────────────────────────────────
DEAL_TYPES = ["discount", "bogo", "freebie", "special", "coupon", "event"]

DEAL_TYPE_DISPLAY = {
    "discount": "1. 💰 Discount / % Off",
    "bogo": "2. 🎁 Buy One Get One",
    "freebie": "3. 🆓 Free Item / Sample",
    "special": "4. ⭐ Daily / Weekly Special",
    "coupon": "5. 🎟️ Coupon Code",
    "event": "6. 🎉 Event / Grand Opening",
}

DEAL_TYPE_LABELS = {
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

# ── Validation limits ──────────────────────────────────────────
MAX_TITLE_LEN = 80
MAX_DESC_LEN = 300

# ── Confirmation vocabulary (includes Hindi affirmatives) ──────
YES_WORDS = {
    "yes", "y", "confirm", "looks good", "correct", "ok",
    "👍", "sure", "yep", "yeah", "yea", "haan", "ji",
    "ha", "sahi hai", "theek hai", "done",
}
NO_WORDS = {"no", "n", "restart", "start over", "nahi", "nah"}

# ── Step order for back navigation ─────────────────────────────
DEAL_STEP_ORDER = [
    "business_lookup", "select_business", "title",
    "description", "deal_type", "duration", "confirm",
]
DEAL_TOTAL_STEPS = 6  # lookup doesn't count as a user "step"


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


# ── Ownership check ────────────────────────────────────────────
def _is_owner(business: dict, wa_id: str) -> bool:
    """Check if wa_id owns this business (source_id prefix match)."""
    source_id = business.get("source_id", "")
    return source_id.startswith(f"user_{wa_id}_")


# ── Back navigation ────────────────────────────────────────────
def _go_back_deal(session: dict, settings: Settings) -> str:
    """Navigate to the previous step in the deal posting flow."""
    current = session["step"]
    wa_id = session["wa_id"]
    data = session.get("data", {})

    try:
        idx = DEAL_STEP_ORDER.index(current)
    except ValueError:
        idx = 0

    # Can't go back from the first step
    if idx <= 0:
        return "You're at the first step. Type *cancel* to exit."

    prev_step = DEAL_STEP_ORDER[idx - 1]

    # Skip SELECT_BUSINESS if we didn't go through it
    if prev_step == "select_business" and not session.get("matches"):
        prev_step = "business_lookup"

    session["step"] = prev_step
    _save(wa_id, session, settings)

    # Return the appropriate prompt for the previous step
    if prev_step == "business_lookup":
        return (
            "Going back...\n\n"
            "Tell me your *business name* or *phone number* "
            "so I can link this deal to your listing."
        )
    elif prev_step == "select_business":
        matches = session.get("matches", [])
        lines = ["Going back...\n\nWhich business? (reply with the number)\n"]
        for i, b in enumerate(matches, 1):
            lines.append(f"{i}. *{b['name']}* — {b.get('city', '')}, {b.get('state', '')}")
        return "\n".join(lines)
    elif prev_step == "title":
        return "Going back...\n\n*Step 1/{total}* — What's the *deal title*?\n(e.g. \"Weekend Dosa Fest\")".format(total=DEAL_TOTAL_STEPS)
    elif prev_step == "description":
        return "Going back...\n\n*Step 2/{total}* — Give me a short *description* of the deal.".format(total=DEAL_TOTAL_STEPS)
    elif prev_step == "deal_type":
        return f"Going back...\n\n*Step 3/{DEAL_TOTAL_STEPS}*\n{_deal_type_menu()}"
    elif prev_step == "duration":
        return f"Going back...\n\n*Step 4/{DEAL_TOTAL_STEPS}*\n{_duration_menu()}"
    return "Going back..."


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
    """
    Extract category keyword from deal search query for filtering.
    Aligned with business_service.CATEGORY_MAP DB values.
    """
    msg = message.lower()
    # Maps user keywords → DB category (same as business_service)
    category_map = {
        "grocery": "grocery",
        "groceries": "grocery",
        "food": "restaurant",
        "restaurant": "restaurant",
        "tiffin": "restaurant",
        "catering": "restaurant",
        "biryani": "restaurant",
        "dosa": "restaurant",
        "salon": "salon",
        "beauty": "salon",
        "spa": "salon",
        "parlor": "salon",
        "parlour": "salon",
        "jewelry": "jeweler",
        "jeweler": "jeweler",
        "jewellery": "jeweler",
        "temple": "temple",
        "tutoring": "tutor",
        "tutor": "tutor",
        "coaching": "tutor",
        "education": "tutor",
        "real estate": "realtor",
        "realtor": "realtor",
        "insurance": "insurance",
        "tax": "cpa",
        "cpa": "cpa",
        "accountant": "cpa",
        "lawyer": "lawyer",
        "legal": "lawyer",
        "attorney": "lawyer",
        "immigration": "lawyer",
        "doctor": "doctor",
        "dentist": "doctor",
        "pharmacy": "doctor",
        "medical": "doctor",
        "healthcare": "doctor",
        "travel": "travel",
        "banquet": "banquet",
        "event": "banquet",
        "childcare": "childcare",
        "daycare": "childcare",
        "nanny": "childcare",
        "driving": "driving",
        "cleaning": "cleaning",
        "photographer": "photographer",
    }
    for keyword, category in category_map.items():
        if keyword in msg:
            return category
    return None


_KEYWORD_STOPWORDS = {
    "in", "near", "around", "show", "find", "deals", "deal",
    "offers", "offer", "discounts", "discount", "coupons", "coupon",
    "promotions", "promotion", "promos", "promo", "sales", "sale",
    "me", "the", "a", "an", "for", "at", "of", "my", "all",
    "any", "browse", "latest", "current", "list", "today",
    "todays", "what", "whats", "on", "with",
}


def _extract_keyword(message: str) -> str | None:
    """
    Extract a search keyword from the deal query after stripping
    city/state, category terms, and stopwords. Returns None if nothing useful.
    """
    msg = message.lower().strip()
    # Strip common preamble phrases
    for strip in [
        "deals near", "deals in", "deals around", "show deals",
        "find deals", "any deals", "browse deals", "latest deals",
        "show me deals", "current deals", "list deals",
        "discounts near", "discounts in", "offers near", "offers in",
    ]:
        msg = msg.replace(strip, "")
    # Strip city/state (best-effort)
    from app.services.business_service import detect_city_state
    city, state = detect_city_state(message)
    if city:
        msg = msg.replace(city.lower(), "")
    if state:
        msg = msg.replace(state.lower(), "")
    # Strip category words (already handled by category filter)
    cat = _extract_category_filter(message)
    if cat:
        msg = msg.replace(cat.lower(), "")
    # Strip stopwords
    words = [w for w in msg.split() if w.strip(",.!?") not in _KEYWORD_STOPWORDS]
    keyword = " ".join(words).strip().strip(",").strip()
    if len(keyword) >= 3:
        return keyword
    return None


# ── Spam filter ────────────────────────────────────────────────
_SPAM_PATTERNS = [
    re.compile(r"!{4,}"),          # 4+ exclamation marks
    re.compile(r"\${3,}"),          # $$$ or more
    re.compile(r"(.)\1{5,}"),       # any char repeated 6+ times
    re.compile(r"(https?://|bit\.ly|tinyurl)", re.I),  # URLs
    re.compile(r"[A-Z]{15,}"),      # 15+ ALL CAPS chars in a row
]


def _check_spam(text: str) -> str | None:
    """
    Returns a rejection message if text looks spammy, else None.
    """
    for pattern in _SPAM_PATTERNS:
        if pattern.search(text):
            return "Please keep your text clean and clear — no excessive punctuation, links, or ALL CAPS. 👍"
    return None


# ── Search cache helpers ──────────────────────────────────────
def _get_cached_deals(cache_key: str, settings: Settings) -> list[dict] | None:
    """Check Redis for cached deal search results."""
    from app.services.session_store import _get_redis
    r = _get_redis(settings)
    if not r:
        return None
    try:
        data = r.get(cache_key)
        if data:
            return json.loads(data)
    except Exception:
        pass
    return None


def _set_cached_deals(cache_key: str, deals: list[dict], settings: Settings) -> None:
    """Cache deal search results in Redis."""
    from app.services.session_store import _get_redis
    r = _get_redis(settings)
    if not r:
        return
    try:
        r.setex(cache_key, _SEARCH_CACHE_TTL, json.dumps(deals, default=str))
    except Exception:
        pass


def _invalidate_deal_cache(city: str | None, settings: Settings) -> None:
    """
    Bust deal search cache after insert/delete/boost.
    Deletes all cache keys matching the city (or all if city unknown).
    """
    from app.services.session_store import _get_redis
    r = _get_redis(settings)
    if not r:
        return
    try:
        pattern = f"deals_cache:{city or ''}:*" if city else "deals_cache:*"
        keys = r.keys(pattern)
        if keys:
            r.delete(*keys)
    except Exception:
        pass  # Best-effort


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
        "so I can link this deal to your listing.\n\n"
        "Type *cancel* anytime to exit or *back* to go to the previous step."
    )


# ── Menu helpers ────────────────────────────────────────────────
def _deal_type_menu() -> str:
    lines = ["What type of deal is this? (reply with the number):\n"]
    for dt in DEAL_TYPES:
        lines.append(DEAL_TYPE_DISPLAY[dt])
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


# ── Duplicate deal detection ───────────────────────────────────
def _check_duplicate_deal(business_id: str, title: str, settings: Settings) -> bool:
    """
    Check if a similar deal was posted for this business in the last 7 days.
    Uses first 15 chars of title for prefix matching.
    Returns True if duplicate found.
    """
    try:
        client = create_client(settings.SUPABASE_URL, settings.SUPABASE_KEY)
        since = (datetime.now(timezone.utc) - timedelta(days=7)).isoformat()
        title_prefix = title[:15].strip() if len(title) >= 15 else title

        result = (
            client.table("deals")
            .select("id, title")
            .eq("business_id", business_id)
            .ilike("title", f"{title_prefix}%")
            .gte("created_at", since)
            .limit(1)
            .execute()
        )
        return bool(result.data)
    except Exception as e:
        logger.warning(f"Duplicate deal check failed: {e}")
        return False  # On failure, allow


# ── Deal limit check ──────────────────────────────────────────
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
                    "You've used your *1 free deal* this month 👍\n\n"
                    "Upgrade to post more and reach more customers:\n\n"
                    "⭐ *Featured* — $15/month (5 deals + top placement)\n"
                    "🚀 *Premium* — $30/month (unlimited deals)\n\n"
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


# ── Audit logging ──────────────────────────────────────────────
def _log_deal_event(event: str, wa_id: str, details: dict, settings: Settings) -> None:
    """Log deal events for audit trail. Best-effort, never blocks."""
    try:
        client = create_client(settings.SUPABASE_URL, settings.SUPABASE_KEY)
        client.table("notification_log").insert({
            "id": str(uuid.uuid4()),
            "business_id": details.get("business_id", ""),
            "business_name": details.get("business_name", ""),
            "owner_wa_id": wa_id,
            "search_query": event,
            "status": "logged",
            "error_msg": str(details)[:500],
        }).execute()
    except Exception:
        pass  # Best-effort


# ── Main handler ────────────────────────────────────────────────
def handle_deal_message(wa_id: str, message: str, settings: Settings) -> str:
    """Process a message within an active deal-posting session."""
    session = _get(wa_id, settings)
    if not session:
        return "No active deal session. Say *'post a deal'* to start."

    msg = message.strip()

    # Global cancel
    if msg.lower() in ("cancel", "stop", "quit", "exit", "nevermind"):
        return cancel_deal_session(wa_id, settings)

    # Global back
    if msg.lower() in ("back", "go back", "previous"):
        return _go_back_deal(session, settings)

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
        # ── Validate title ──
        title = msg.strip()
        if len(title) < 3:
            return "Title is too short. Give me at least a few words.\n(e.g. \"Weekend Dosa Fest\")"
        if len(title) > MAX_TITLE_LEN:
            return f"Title is too long ({len(title)} chars). Please keep it under {MAX_TITLE_LEN} characters."
        spam_msg = _check_spam(title)
        if spam_msg:
            return spam_msg

        data["title"] = title
        session["data"] = data
        session["step"] = DealStep.DESCRIPTION
        _save(wa_id, session, settings)
        return (
            f"Deal title: *{title}*\n\n"
            f"*Step 2/{DEAL_TOTAL_STEPS}* — Now give me a short *description* of the deal.\n"
            "(e.g. \"20% off all dosas this weekend\" or \"Free chai with any lunch combo\")\n\n"
            f"_Max {MAX_DESC_LEN} characters_"
        )

    elif step == DealStep.DESCRIPTION:
        # ── Validate description ──
        desc = msg.strip()
        if len(desc) < 5:
            return "Description is too short. Give a bit more detail about the deal."
        if len(desc) > MAX_DESC_LEN:
            return f"Description is too long ({len(desc)} chars). Please keep it under {MAX_DESC_LEN} characters."
        spam_msg = _check_spam(desc)
        if spam_msg:
            return spam_msg

        data["description"] = desc
        session["data"] = data
        session["step"] = DealStep.DEAL_TYPE
        _save(wa_id, session, settings)
        return f"Got it!\n\n*Step 3/{DEAL_TOTAL_STEPS}*\n{_deal_type_menu()}"

    elif step == DealStep.DEAL_TYPE:
        dt = _parse_deal_type(msg)
        if not dt:
            return f"I didn't catch that.\n\n{_deal_type_menu()}"
        data["deal_type"] = dt
        session["data"] = data
        session["step"] = DealStep.DURATION
        _save(wa_id, session, settings)
        return f"Type: *{DEAL_TYPE_LABELS[dt]}*\n\n*Step 4/{DEAL_TOTAL_STEPS}*\n{_duration_menu()}"

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
        lower = msg.lower().strip()
        if lower in YES_WORDS:
            return _insert_deal(session, settings)
        elif lower in NO_WORDS:
            session["step"] = DealStep.TITLE
            session["data"] = {k: v for k, v in data.items() if k in ("business",)}
            session.pop("_duplicate_warned", None)
            _save(wa_id, session, settings)
            return (
                "No problem! Let's start over.\n\n"
                f"*Step 1/{DEAL_TOTAL_STEPS}* — What's the *deal title*?\n"
                "(e.g. \"Weekend Dosa Fest\")"
            )
        else:
            return "Please reply *yes* to post or *no* to start over."

    return "Something went wrong. Type *cancel* to exit."


# ── Business lookup for deal posting ────────────────────────────
def _deal_lookup_business(session: dict, msg: str, settings: Settings) -> str:
    """
    Find the business this deal belongs to.
    FIX #1: Only returns businesses OWNED by this user (source_id prefix match).
    FIX #2: Checks deal limit BEFORE continuing the flow.
    """
    wa_id = session["wa_id"]
    data = session.get("data", {})
    owner_prefix = f"user_{wa_id}_"

    try:
        client = create_client(settings.SUPABASE_URL, settings.SUPABASE_KEY)
        digits = "".join(c for c in msg if c.isdigit())

        if len(digits) >= 7:
            result = (
                client.table("businesses")
                .select("*")
                .ilike("phone", f"%{digits[-10:]}%")
                .ilike("source_id", f"{owner_prefix}%")
                .execute()
            )
        else:
            result = (
                client.table("businesses")
                .select("*")
                .ilike("name", f"%{msg}%")
                .ilike("source_id", f"{owner_prefix}%")
                .execute()
            )

        if not result.data:
            # Check if business exists but isn't owned by this user
            if len(digits) >= 7:
                any_match = client.table("businesses").select("name").ilike("phone", f"%{digits[-10:]}%").limit(1).execute()
            else:
                any_match = client.table("businesses").select("name").ilike("name", f"%{msg}%").limit(1).execute()

            if any_match.data:
                return (
                    f"I found *{any_match.data[0]['name']}*, but it's not linked to your account.\n\n"
                    "You can only post deals for businesses you registered.\n"
                    "Need to register? Type *'add my business'*.\n\n"
                    "Type *cancel* to exit."
                )
            return (
                "I couldn't find that business in our directory.\n"
                "Please check the name/phone, or *add your business first* by typing *'add my business'*.\n\n"
                "Type *cancel* to exit."
            )

        # ── Single match → check limit then proceed ──
        if len(result.data) == 1:
            biz = result.data[0]

            # FIX #2: Check deal limit UPFRONT
            limit_msg = _check_deal_limit(biz["id"], wa_id, settings)
            if limit_msg:
                _del(wa_id, settings)
                return limit_msg

            data["business"] = biz
            session["data"] = data
            session["step"] = DealStep.TITLE
            _save(wa_id, session, settings)
            return (
                f"Found: *{biz['name']}* — {biz.get('city', '')}, {biz.get('state', '')}\n\n"
                f"*Step 1/{DEAL_TOTAL_STEPS}* — What's the *deal title*?\n"
                "(e.g. \"Weekend Dosa Fest\" or \"Grand Opening — 30% Off\")"
            )

        # ── Multiple matches → let user pick ──
        session["matches"] = result.data[:5]
        session["step"] = DealStep.SELECT_BUSINESS
        _save(wa_id, session, settings)
        lines = ["You have multiple businesses. Which one? (reply with the number)\n"]
        for i, b in enumerate(result.data[:5], 1):
            lines.append(f"{i}. *{b['name']}* — {b.get('city', '')}, {b.get('state', '')}")
        return "\n".join(lines)

    except Exception as e:
        logger.error(f"Deal business lookup failed for {wa_id}: {e}")
        # FIX #4: Don't delete session on transient error
        return "Sorry, something went wrong looking up your business. Please try again. 🙏"


def _deal_select_business(session: dict, msg: str, settings: Settings) -> str:
    wa_id = session["wa_id"]
    matches = session.get("matches", [])
    data = session.get("data", {})
    try:
        idx = int(msg.strip()) - 1
        if 0 <= idx < len(matches):
            biz = matches[idx]

            # FIX #2: Check deal limit UPFRONT on selection too
            limit_msg = _check_deal_limit(biz["id"], wa_id, settings)
            if limit_msg:
                _del(wa_id, settings)
                return limit_msg

            data["business"] = biz
            session["data"] = data
            session["step"] = DealStep.TITLE
            _save(wa_id, session, settings)
            return (
                f"Selected: *{biz['name']}*\n\n"
                f"*Step 1/{DEAL_TOTAL_STEPS}* — What's the *deal title*?\n"
                "(e.g. \"Weekend Dosa Fest\" or \"Grand Opening — 30% Off\")"
            )
    except (ValueError, IndexError):
        pass
    return "Please reply with a number from the list, or type *cancel* to exit."


# ── Confirmation & insert ───────────────────────────────────────
def _deal_confirmation(data: dict) -> str:
    b = data["business"]
    return (
        f"*Step {DEAL_TOTAL_STEPS}/{DEAL_TOTAL_STEPS}* — Here's your deal:\n\n"
        f"🏪 *{b['name']}*\n"
        f"🏷️ *{data['title']}*\n"
        f"📝 {data['description']}\n"
        f"📂 {DEAL_TYPE_LABELS.get(data['deal_type'], data['deal_type'])}\n"
        f"⏰ Runs for {data['duration_label']}\n\n"
        "Reply *yes* to post or *no* to start over."
    )


def _insert_deal(session: dict, settings: Settings) -> str:
    """Insert the deal into Supabase."""
    data = session["data"]
    wa_id = session["wa_id"]
    b = data["business"]
    now = datetime.now(timezone.utc)
    duration_seconds = data.get("duration_seconds", 86400)  # default 1 day
    expires = now + timedelta(seconds=duration_seconds)

    # ── FIX #7: Duplicate deal detection ──
    if not session.get("_duplicate_warned"):
        is_dup = _check_duplicate_deal(b["id"], data["title"], settings)
        if is_dup:
            session["_duplicate_warned"] = True
            _save(wa_id, session, settings)
            return (
                "⚠️ You posted a similar deal for this business in the last 7 days.\n\n"
                "Reply *yes* to post anyway, or *no* to start over."
            )

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

        # Bust search cache so new deal appears immediately
        _invalidate_deal_cache(b.get("city"), settings)

        # Audit log
        _log_deal_event("deal_posted", wa_id, {
            "business_id": b["id"],
            "business_name": b["name"],
            "deal_title": data["title"],
            "deal_type": data["deal_type"],
            "duration": data.get("duration_label", ""),
        }, settings)

        # Check if business is featured for upgrade nudge
        is_featured = b.get("is_featured", False)
        upgrade_nudge = ""
        if not is_featured:
            upgrade_nudge = (
                "\n\n💡 Want more people to see this?\n"
                "🚀 *Boost this deal* — $5 for 24 hours at the top\n"
                "⭐ *Featured* — $15/month (top placement + daily digest)\n"
                "🚀 *Premium* — $30/month (unlimited deals + priority)\n"
                "👉 Reply *\"boost\"* or *\"upgrade\"* to activate"
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
        # FIX #4: Keep session alive on transient error
        return (
            "Sorry, something went wrong while posting your deal. 🙏\n\n"
            "Your data is saved — reply *yes* to try again, or *cancel* to exit."
        )


# ── Deal search / browse (for users) ───────────────────────────
def search_deals(
    message: str,
    settings: Settings,
    limit: int = 5,
    today_only: bool = False,
    offset: int = 0,
) -> list[dict]:
    """
    Search active deals by city, state, category, or keyword.
    Supports today-only filtering, category extraction, keyword search,
    and offset-based pagination for "show more" browsing.
    Results are ranked: featured first, then by recency + urgency.
    Cached in Redis for 3 min to reduce DB load.
    """
    # ── Build cache key ──
    from app.services.business_service import detect_city_state
    city, state = detect_city_state(message)
    cat_filter = _extract_category_filter(message)
    keyword = _extract_keyword(message)
    cache_key = f"deals_cache:{city or ''}:{state or ''}:{cat_filter or ''}:{keyword or ''}:{today_only}:{offset}"

    # ── Check cache first ──
    cached = _get_cached_deals(cache_key, settings)
    if cached is not None:
        return cached

    try:
        client = create_client(settings.SUPABASE_URL, settings.SUPABASE_KEY)
        now = datetime.now(timezone.utc)
        now_iso = now.isoformat()

        # Fetch more for ranking + pagination
        fetch_limit = (limit + offset) * 3
        query = (
            client.table("deals")
            .select("*")
            .eq("is_active", True)
            .gte("expires_at", now_iso)
            .order("created_at", desc=True)
            .limit(fetch_limit)
        )

        # City/state filter
        if city:
            query = query.ilike("city", f"%{city}%")
        if state:
            query = query.eq("state", state.upper())

        # Category filter (aligned with business_service categories)
        if cat_filter:
            query = query.ilike("category", f"%{cat_filter}%")

        # Keyword search on title + description
        if keyword:
            query = query.or_(
                f"title.ilike.%{keyword}%,description.ilike.%{keyword}%"
            )

        # Today-only: deals expiring within 24 hours
        if today_only:
            end_of_today = (now + timedelta(hours=24)).isoformat()
            query = query.lte("expires_at", end_of_today)

        result = query.execute()
        deals = result.data if result.data else []

        if not deals:
            _set_cached_deals(cache_key, [], settings)
            return []

        # ── Ranking: featured businesses get boost ──
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

        # ── Scoring with boost + featured + urgency ──
        def _deal_score(deal: dict) -> float:
            score = 0.0

            # Boosted deal (+80, only if boost hasn't expired)
            boost_until = deal.get("boosted_until", "")
            if boost_until:
                try:
                    boost_dt = datetime.fromisoformat(boost_until.replace("Z", "+00:00"))
                    if boost_dt > now:
                        score += 80
                        deal["is_boosted"] = True
                        # Calculate hours remaining for display
                        deal["boost_hours_left"] = round((boost_dt - now).total_seconds() / 3600, 1)
                    else:
                        deal["is_boosted"] = False
                except Exception:
                    deal["is_boosted"] = False
            else:
                deal["is_boosted"] = False

            # Featured boost (+100)
            if deal.get("business_id") in featured_ids:
                score += 100
                deal["is_featured_business"] = True
            else:
                deal["is_featured_business"] = False

            # Recency boost (newer = higher, max +50, decays 1pt/hour)
            created = deal.get("created_at", "")
            if created:
                try:
                    cr_dt = datetime.fromisoformat(created.replace("Z", "+00:00"))
                    age_hours = (now - cr_dt).total_seconds() / 3600
                    score += max(0, 50 - age_hours)
                except Exception:
                    pass

            # Urgency boost — smooth curve: max +30, linear decay over 168h
            expires = deal.get("expires_at", "")
            if expires:
                try:
                    exp_dt = datetime.fromisoformat(expires.replace("Z", "+00:00"))
                    hours_left = (exp_dt - now).total_seconds() / 3600
                    if hours_left > 0:
                        score += max(0, 30 * (1 - hours_left / 168))
                except Exception:
                    pass

            return score

        deals.sort(key=_deal_score, reverse=True)
        page = deals[offset:offset + limit]

        # Cache the page
        _set_cached_deals(cache_key, page, settings)
        return page

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


# ── FIX #10: Better no-results messages ─────────────────────────
def format_deals_for_whatsapp(deals: list[dict], query_type: str = "all") -> str:
    """Format deals as a WhatsApp-friendly message for browsing."""
    if not deals:
        if query_type == "today":
            return (
                "No deals expiring today — but that's a good thing! 😄\n\n"
                "Try *'show deals'* to see everything active.\n"
                "Or *'post a deal'* if you're a business owner!"
            )
        return (
            "No active deals found in that area right now.\n\n"
            "💡 *Know a desi business with a deal?* Tell them about Mira!\n"
            "Or try a different city — e.g. *'deals in Dallas'*"
        )

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
        # Boost badge + countdown
        boost_badge = ""
        if d.get("is_boosted"):
            hours_left = d.get("boost_hours_left", 0)
            if hours_left > 1:
                boost_badge = f" 🚀 BOOSTED ({int(hours_left)}h left)"
            else:
                boost_badge = " 🚀 BOOSTED"
        lines.append(
            f"*{i}. {freshness}{d['title']}*{featured}{boost_badge}\n"
            f"   🏪 {d['business_name']}\n"
            f"   📝 {d['description']}\n"
            f"   📍 {d.get('city', '')}, {d.get('state', '')}"
            f"{expire_line}\n"
        )
    lines.append(
        "📄 *More deals?* Say *\"more deals\"*\n"
        "Want to post your own? Say *\"post a deal\"*!"
    )
    return "\n".join(lines)


# ── "More deals" intent detection ──────────────────────────────
def detect_more_deals_intent(message: str) -> bool:
    """Check if user wants to see more deals (pagination)."""
    msg = message.lower().strip()
    return msg in (
        "more deals", "show more", "more", "next deals",
        "next page", "show more deals", "aur dikhao",
    )


# ── Deal deletion (owner only) ─────────────────────────────────
def delete_deal(wa_id: str, deal_title_or_idx: str, settings: Settings) -> str:
    """
    Delete a deal posted by this user.
    Accepts a title fragment or deal number from most recent listing.
    """
    try:
        client = create_client(settings.SUPABASE_URL, settings.SUPABASE_KEY)

        # Find active deals posted by this user
        result = (
            client.table("deals")
            .select("id, title, business_name")
            .eq("posted_by_wa_id", wa_id)
            .eq("is_active", True)
            .order("created_at", desc=True)
            .limit(10)
            .execute()
        )

        if not result.data:
            return "You don't have any active deals to delete."

        # Try to match by number or title fragment
        target = None
        try:
            idx = int(deal_title_or_idx.strip()) - 1
            if 0 <= idx < len(result.data):
                target = result.data[idx]
        except ValueError:
            # Match by title fragment
            frag = deal_title_or_idx.lower().strip()
            for d in result.data:
                if frag in d["title"].lower():
                    target = d
                    break

        if not target:
            # List their deals for selection
            lines = ["Which deal do you want to delete? (reply with the number)\n"]
            for i, d in enumerate(result.data, 1):
                lines.append(f"{i}. *{d['title']}* — {d['business_name']}")
            lines.append("\nOr type *cancel* to exit.")
            return "\n".join(lines)

        # Deactivate the deal (soft delete)
        client.table("deals").update({"is_active": False}).eq("id", target["id"]).execute()

        # Bust search cache
        _invalidate_deal_cache(None, settings)  # city unknown here, bust all

        _log_deal_event("deal_deleted", wa_id, {
            "deal_id": target["id"],
            "deal_title": target["title"],
            "business_name": target["business_name"],
        }, settings)

        return (
            f"Deal *{target['title']}* has been removed. 👍\n\n"
            "Want to post a new one? Say *\"post a deal\"*"
        )

    except Exception as e:
        logger.error(f"Deal deletion failed for {wa_id}: {e}")
        return "Sorry, something went wrong. Please try again. 🙏"


def detect_delete_deal_intent(message: str) -> bool:
    """Check if user wants to delete a deal."""
    msg = message.lower().strip()
    return any(phrase in msg for phrase in [
        "delete deal", "remove deal", "delete my deal",
        "remove my deal", "cancel deal", "cancel my deal",
    ])


# ── Boost deal ────────────────────────────────────────────────
def detect_boost_intent(message: str) -> bool:
    """Check if user wants to boost a deal."""
    msg = message.lower().strip()
    return msg in ("boost", "boost deal", "boost my deal")


def boost_deal(wa_id: str, settings: Settings) -> str:
    """
    Mark the user's most recent deal as boosted for 24 hours.
    In production, this would integrate with Stripe payment link first.
    For now, marks the deal and returns a payment CTA.
    """
    try:
        client = create_client(settings.SUPABASE_URL, settings.SUPABASE_KEY)

        # Find the most recent active deal by this user
        result = (
            client.table("deals")
            .select("id, title, business_name, city, boosted_until")
            .eq("posted_by_wa_id", wa_id)
            .eq("is_active", True)
            .order("created_at", desc=True)
            .limit(1)
            .execute()
        )

        if not result.data:
            return (
                "You don't have any active deals to boost.\n"
                "Post a deal first with *\"post a deal\"*"
            )

        deal = result.data[0]
        now = datetime.now(timezone.utc)

        # Check if already boosted
        if deal.get("boosted_until"):
            try:
                boost_dt = datetime.fromisoformat(deal["boosted_until"].replace("Z", "+00:00"))
                if boost_dt > now:
                    hours_left = round((boost_dt - now).total_seconds() / 3600, 1)
                    return (
                        f"Your deal *{deal['title']}* is already boosted! 🚀\n"
                        f"⏰ {hours_left} hours remaining\n\n"
                        "It's appearing at the top of search results."
                    )
            except Exception:
                pass

        # Mark as boosted (24 hours)
        boost_until = (now + timedelta(hours=24)).isoformat()
        client.table("deals").update({
            "boosted_until": boost_until,
        }).eq("id", deal["id"]).execute()

        # Bust cache so boosted deal shows up immediately
        _invalidate_deal_cache(deal.get("city"), settings)

        _log_deal_event("deal_boosted", wa_id, {
            "deal_id": deal["id"],
            "deal_title": deal["title"],
            "business_name": deal["business_name"],
            "boost_until": boost_until,
        }, settings)

        return (
            f"🚀 *{deal['title']}* is now boosted for 24 hours!\n\n"
            "Your deal will appear at the top of search results "
            "and in the daily digest.\n\n"
            "⏰ Boost expires tomorrow at this time.\n\n"
            "💳 *$5 boost fee* — payment link coming to your WhatsApp shortly."
        )

    except Exception as e:
        logger.error(f"Deal boost failed for {wa_id}: {e}")
        return "Sorry, something went wrong. Please try again. 🙏"


# ── Pagination state helpers ──────────────────────────────────
def get_user_deal_offset(wa_id: str, settings: Settings) -> int:
    """Get the current pagination offset for this user's deal browsing."""
    from app.services.session_store import _get_redis
    r = _get_redis(settings)
    if not r:
        return 0
    try:
        val = r.get(f"deal_offset:{wa_id}")
        return int(val) if val else 0
    except Exception:
        return 0


def increment_user_deal_offset(wa_id: str, settings: Settings, step: int = 5) -> int:
    """Increment and return the new offset for "more deals" pagination."""
    from app.services.session_store import _get_redis
    r = _get_redis(settings)
    if not r:
        return step
    try:
        key = f"deal_offset:{wa_id}"
        new_val = r.incrby(key, step)
        r.expire(key, 600)  # 10 min TTL — resets after inactivity
        return new_val
    except Exception:
        return step


def reset_user_deal_offset(wa_id: str, settings: Settings) -> None:
    """Reset pagination offset (called on new browse query)."""
    from app.services.session_store import _get_redis
    r = _get_redis(settings)
    if not r:
        return
    try:
        r.delete(f"deal_offset:{wa_id}")
    except Exception:
        pass


# ══════════════════════════════════════════════════════════════════
# AUTO-EXPIRY + RE-ENGAGEMENT + ORPHAN CLEANUP
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


async def cleanup_orphan_deals(settings: Settings) -> dict:
    """
    Deactivate deals whose parent business no longer exists.
    Called daily after expire_stale_deals.
    """
    try:
        client = create_client(settings.SUPABASE_URL, settings.SUPABASE_KEY)

        # Get all active deals' business_ids
        active_deals = (
            client.table("deals")
            .select("id, business_id")
            .eq("is_active", True)
            .limit(500)
            .execute()
        )
        if not active_deals.data:
            return {"orphans": 0}

        biz_ids = list({d["business_id"] for d in active_deals.data if d.get("business_id")})
        if not biz_ids:
            return {"orphans": 0}

        # Check which business_ids still exist
        existing = (
            client.table("businesses")
            .select("id")
            .in_("id", biz_ids)
            .execute()
        )
        existing_ids = {b["id"] for b in (existing.data or [])}

        # Deactivate orphans
        orphan_count = 0
        for d in active_deals.data:
            if d.get("business_id") and d["business_id"] not in existing_ids:
                client.table("deals").update({"is_active": False}).eq("id", d["id"]).execute()
                orphan_count += 1

        if orphan_count:
            logger.info(f"Cleaned up {orphan_count} orphan deals")
        return {"orphans": orphan_count}

    except Exception as e:
        logger.error(f"Orphan deal cleanup failed: {e}")
        return {"error": str(e)}
