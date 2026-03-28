"""
Hello Desi — Deals & Promotions Service

Handles:
  1. Business owners posting deals via WhatsApp conversation
  2. Users searching/browsing deals by city, category, or keyword
  3. Auto-expiry of old deals

Uses in-memory session state for the multi-step deal posting flow.
"""

import logging
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from enum import Enum

from supabase import create_client
from config.settings import Settings

logger = logging.getLogger(__name__)

SESSION_TIMEOUT = 600  # 10 minutes

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


@dataclass
class DealSession:
    wa_id: str
    step: str
    data: dict = field(default_factory=dict)
    matches: list = field(default_factory=list)
    updated_at: float = field(default_factory=time.time)


# ── In-memory session store ─────────────────────────────────────
_deal_sessions: dict[str, DealSession] = {}


def _clean_expired():
    now = time.time()
    expired = [k for k, v in _deal_sessions.items() if now - v.updated_at > SESSION_TIMEOUT]
    for k in expired:
        del _deal_sessions[k]


def has_active_deal_session(wa_id: str) -> bool:
    _clean_expired()
    return wa_id in _deal_sessions


def cancel_deal_session(wa_id: str) -> str:
    if wa_id in _deal_sessions:
        del _deal_sessions[wa_id]
    return "Deal posting cancelled. How else can I help? 🙏"


# ── Intent detection ────────────────────────────────────────────
def detect_deal_intent(message: str) -> str | None:
    """
    Detect if user wants to post a deal or browse deals.
    Returns "post", "browse", or None.
    """
    msg = message.lower().strip()

    post_phrases = [
        "post a deal", "post deal", "add a deal", "add deal",
        "create a deal", "new deal", "post promotion", "add promotion",
        "post a promotion", "post my deal", "create promotion",
        "post an offer", "add an offer", "post offer",
        "i have a deal", "i have a promotion", "share a deal",
    ]
    browse_phrases = [
        "deals near me", "any deals", "show deals", "find deals",
        "deals in", "promotions near", "promotions in", "offers near",
        "offers in", "today's deals", "todays deals", "current deals",
        "what deals", "show me deals", "browse deals", "latest deals",
        "any offers", "any promotions", "deals around",
    ]

    for phrase in post_phrases:
        if phrase in msg:
            return "post"
    for phrase in browse_phrases:
        if phrase in msg:
            return "browse"
    return None


# ── Start deal posting flow ─────────────────────────────────────
def start_deal_flow(wa_id: str) -> str:
    """Begin the 'post a deal' conversation."""
    _deal_sessions[wa_id] = DealSession(
        wa_id=wa_id,
        step=DealStep.BUSINESS_LOOKUP,
    )
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
    _clean_expired()
    session = _deal_sessions.get(wa_id)
    if not session:
        return "No active deal session. Say *'post a deal'* to start."

    msg = message.strip()
    if msg.lower() in ("cancel", "stop", "quit", "exit", "nevermind"):
        return cancel_deal_session(wa_id)

    session.updated_at = time.time()
    return _handle_deal_step(session, msg, settings)


# ── Deal posting step handler ───────────────────────────────────
def _handle_deal_step(session: DealSession, msg: str, settings: Settings) -> str:
    step = session.step

    if step == DealStep.BUSINESS_LOOKUP:
        return _deal_lookup_business(session, msg, settings)

    elif step == DealStep.SELECT_BUSINESS:
        return _deal_select_business(session, msg)

    elif step == DealStep.TITLE:
        session.data["title"] = msg
        session.step = DealStep.DESCRIPTION
        return (
            f"Deal title: *{msg}*\n\n"
            "Now give me a short *description* of the deal.\n"
            "(e.g. \"20% off all dosas this weekend\" or \"Free chai with any lunch combo\")"
        )

    elif step == DealStep.DESCRIPTION:
        session.data["description"] = msg
        session.step = DealStep.DEAL_TYPE
        return f"Got it!\n\n{_deal_type_menu()}"

    elif step == DealStep.DEAL_TYPE:
        dt = _parse_deal_type(msg)
        if not dt:
            return f"I didn't catch that.\n\n{_deal_type_menu()}"
        session.data["deal_type"] = dt
        session.step = DealStep.DURATION
        return f"Type: *{DEAL_TYPE_DISPLAY[dt]}*\n\n{_duration_menu()}"

    elif step == DealStep.DURATION:
        result = _parse_duration(msg)
        if not result:
            return f"Please pick a number.\n\n{_duration_menu()}"
        label, delta = result
        session.data["duration_label"] = label
        session.data["duration_delta"] = delta
        session.step = DealStep.CONFIRM
        return _deal_confirmation(session)

    elif step == DealStep.CONFIRM:
        if msg.lower() in ("yes", "y", "confirm", "looks good", "correct", "ok", "👍"):
            return _insert_deal(session, settings)
        elif msg.lower() in ("no", "n", "restart", "start over"):
            session.step = DealStep.TITLE
            session.data = {k: v for k, v in session.data.items() if k in ("business",)}
            return "No problem! Let's start over.\n\nWhat's the *deal title*? (e.g. \"Weekend Dosa Fest\")"
        else:
            return "Please reply *yes* to post or *no* to start over."

    return "Something went wrong. Type 'cancel' to exit."


# ── Business lookup for deal posting ────────────────────────────
def _deal_lookup_business(session: DealSession, msg: str, settings: Settings) -> str:
    """Find the business this deal belongs to."""
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
            session.data["business"] = result.data[0]
            session.step = DealStep.TITLE
            b = result.data[0]
            return (
                f"Found: *{b['name']}* — {b.get('city', '')}, {b.get('state', '')}\n\n"
                "What's the *deal title*?\n"
                "(e.g. \"Weekend Dosa Fest\" or \"Grand Opening — 30% Off\")"
            )

        # Multiple matches
        session.matches = result.data[:5]
        session.step = DealStep.SELECT_BUSINESS
        lines = ["Multiple matches found. Which one? (reply with the number)\n"]
        for i, b in enumerate(session.matches, 1):
            lines.append(f"{i}. *{b['name']}* — {b.get('city', '')}, {b.get('state', '')}")
        return "\n".join(lines)

    except Exception as e:
        logger.error(f"Deal business lookup failed for {session.wa_id}: {e}")
        return "Sorry, something went wrong. Please try again. 🙏"


def _deal_select_business(session: DealSession, msg: str) -> str:
    try:
        idx = int(msg.strip()) - 1
        if 0 <= idx < len(session.matches):
            session.data["business"] = session.matches[idx]
            session.step = DealStep.TITLE
            b = session.matches[idx]
            return (
                f"Selected: *{b['name']}*\n\n"
                "What's the *deal title*?\n"
                "(e.g. \"Weekend Dosa Fest\" or \"Grand Opening — 30% Off\")"
            )
    except (ValueError, IndexError):
        pass
    return "Please reply with a number from the list, or type *cancel* to exit."


# ── Confirmation & insert ───────────────────────────────────────
def _deal_confirmation(session: DealSession) -> str:
    d = session.data
    b = d["business"]
    return (
        "Here's your deal:\n\n"
        f"🏪 *{b['name']}*\n"
        f"🏷️ *{d['title']}*\n"
        f"📝 {d['description']}\n"
        f"📂 {DEAL_TYPE_DISPLAY.get(d['deal_type'], d['deal_type'])}\n"
        f"⏰ Runs for {d['duration_label']}\n\n"
        "Reply *yes* to post or *no* to start over."
    )


def _insert_deal(session: DealSession, settings: Settings) -> str:
    """Insert the deal into Supabase."""
    d = session.data
    b = d["business"]
    now = datetime.now(timezone.utc)
    expires = now + d["duration_delta"]

    try:
        client = create_client(settings.SUPABASE_URL, settings.SUPABASE_KEY)
        row = {
            "id": str(uuid.uuid4()),
            "business_id": b["id"],
            "business_name": b["name"],
            "title": d["title"],
            "description": d["description"],
            "deal_type": d["deal_type"],
            "category": b.get("category"),
            "city": b.get("city"),
            "state": b.get("state"),
            "starts_at": now.isoformat(),
            "expires_at": expires.isoformat(),
            "is_active": True,
            "posted_by_wa_id": session.wa_id,
        }
        client.table("deals").insert(row).execute()
        del _deal_sessions[session.wa_id]
        logger.info(f"Deal posted by {session.wa_id}: {d['title']} for {b['name']}")
        return (
            "Your deal is live on Hello Desi! 🎉🔥\n\n"
            f"*{d['title']}* — {b['name']}\n"
            f"Expires in {d['duration_label']}.\n\n"
            "Users searching in your area will see this deal.\n"
            "Want to post another? Say *'post a deal'*."
        )
    except Exception as e:
        logger.error(f"Failed to insert deal for {session.wa_id}: {e}")
        del _deal_sessions[session.wa_id]
        return "Sorry, something went wrong while posting your deal. Please try again later. 🙏"


# ── Deal search / browse (for users) ───────────────────────────
def search_deals(message: str, settings: Settings, limit: int = 5) -> list[dict]:
    """
    Search active deals by city, state, category, or keyword.
    Returns a list of deal dicts.
    """
    try:
        client = create_client(settings.SUPABASE_URL, settings.SUPABASE_KEY)
        now = datetime.now(timezone.utc).isoformat()

        query = (
            client.table("deals")
            .select("*")
            .eq("is_active", True)
            .gte("expires_at", now)
            .order("created_at", desc=True)
            .limit(limit)
        )

        # Try to extract city from message
        from app.services.business_service import detect_city_state
        city, state = detect_city_state(message)
        if city:
            query = query.ilike("city", f"%{city}%")
        if state:
            query = query.eq("state", state.upper())

        result = query.execute()
        return result.data if result.data else []
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


def format_deals_for_whatsapp(deals: list[dict]) -> str:
    """Format deals as a WhatsApp-friendly message for browsing."""
    if not deals:
        return "No active deals found in that area right now. Check back soon! 🙏"

    lines = ["🔥 *Active Deals & Promotions* 🔥\n"]
    for i, d in enumerate(deals, 1):
        expires = d.get("expires_at", "")
        expire_str = ""
        if expires:
            try:
                exp_dt = datetime.fromisoformat(expires.replace("Z", "+00:00"))
                days_left = (exp_dt - datetime.now(timezone.utc)).days
                if days_left <= 0:
                    expire_str = "⏰ Expires today!"
                elif days_left == 1:
                    expire_str = "⏰ 1 day left"
                else:
                    expire_str = f"⏰ {days_left} days left"
            except Exception:
                pass
        expire_line = f"\n   {expire_str}" if expire_str else ""
        lines.append(
            f"*{i}. {d['title']}*\n"
            f"   🏪 {d['business_name']}\n"
            f"   📝 {d['description']}\n"
            f"   📍 {d.get('city', '')}, {d.get('state', '')}"
            f"{expire_line}\n"
        )
    lines.append("Want to post your own deal? Say *'post a deal'*!")
    return "\n".join(lines)
