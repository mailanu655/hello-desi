"""
Mira — Business Registration & Update Service

Handles multi-step WhatsApp conversations for:
  1. Adding a new business listing
  2. Updating an existing business listing

Uses in-memory session state keyed by WhatsApp ID (wa_id).
Each session expires after 10 minutes of inactivity.
"""

import logging
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum

from supabase import create_client
from config.settings import Settings

logger = logging.getLogger(__name__)

# ── Session timeout ──────────────────────────────────────────────
SESSION_TIMEOUT = 600  # 10 minutes

# ── Valid categories (must match DB values) ──────────────────────
VALID_CATEGORIES = [
    "restaurant", "grocery", "temple", "doctor", "lawyer",
    "cpa", "realtor", "travel", "insurance", "salon",
    "jeweler", "banquet",
]

CATEGORY_DISPLAY = {
    "restaurant": "🍛 Restaurant",
    "grocery": "🛒 Grocery Store",
    "temple": "🛕 Temple / Place of Worship",
    "doctor": "🩺 Doctor / Medical",
    "lawyer": "⚖️ Lawyer / Attorney",
    "cpa": "📊 CPA / Accountant",
    "realtor": "🏠 Realtor / Real Estate",
    "travel": "✈️ Travel Agent",
    "insurance": "🛡️ Insurance Agent",
    "salon": "💇 Salon / Beauty",
    "jeweler": "💎 Jeweler",
    "banquet": "🎉 Banquet / Event Venue",
}


# ── Flow steps ───────────────────────────────────────────────────
class AddStep(str, Enum):
    NAME = "name"
    CATEGORY = "category"
    ADDRESS = "address"
    CITY = "city"
    STATE = "state"
    PHONE = "phone"
    CONFIRM = "confirm"


class UpdateStep(str, Enum):
    LOOKUP = "lookup"           # ask for business name or phone
    SELECT = "select"           # if multiple matches, pick one
    CHOOSE_FIELD = "choose_field"  # what do you want to update?
    NEW_VALUE = "new_value"     # enter new value
    CONFIRM = "confirm"


@dataclass
class RegistrationSession:
    wa_id: str
    flow: str  # "add" or "update"
    step: str
    data: dict = field(default_factory=dict)
    matches: list = field(default_factory=list)  # for update flow
    updated_at: float = field(default_factory=time.time)


# ── In-memory session store ──────────────────────────────────────
_sessions: dict[str, RegistrationSession] = {}


def _clean_expired():
    """Remove sessions older than SESSION_TIMEOUT."""
    now = time.time()
    expired = [k for k, v in _sessions.items() if now - v.updated_at > SESSION_TIMEOUT]
    for k in expired:
        del _sessions[k]


def has_active_session(wa_id: str) -> bool:
    """Check if user has an active registration/update session."""
    _clean_expired()
    return wa_id in _sessions


def cancel_session(wa_id: str) -> str:
    """Cancel an active session."""
    if wa_id in _sessions:
        del _sessions[wa_id]
    return "Registration cancelled. How else can I help you? 🙏"


# ── Intent detection ─────────────────────────────────────────────
def detect_registration_intent(message: str) -> str | None:
    """
    Detect if the user wants to add or update a business.
    Returns "add", "update", or None.
    """
    msg = message.lower().strip()

    add_phrases = [
        "add my business", "list my business", "register my business",
        "add a business", "add business", "new listing",
        "i want to add", "i want to list", "add my shop",
        "register my shop", "list my shop", "add my store",
    ]
    update_phrases = [
        "update my business", "edit my business", "change my business",
        "update my listing", "edit my listing", "modify my business",
        "update business", "edit listing", "change my listing",
        "update my shop", "correct my listing", "fix my listing",
    ]

    for phrase in add_phrases:
        if phrase in msg:
            return "add"
    for phrase in update_phrases:
        if phrase in msg:
            return "update"
    return None


# ── Start flows ──────────────────────────────────────────────────
def start_add_flow(wa_id: str) -> str:
    """Begin the 'add my business' conversation."""
    _sessions[wa_id] = RegistrationSession(
        wa_id=wa_id,
        flow="add",
        step=AddStep.NAME,
    )
    return (
        "Great, let's add your business to Mira! 🎉\n\n"
        "I'll ask you a few quick questions.\n\n"
        "What is your *business name*?"
    )


def start_update_flow(wa_id: str) -> str:
    """Begin the 'update my business' conversation."""
    _sessions[wa_id] = RegistrationSession(
        wa_id=wa_id,
        flow="update",
        step=UpdateStep.LOOKUP,
    )
    return (
        "Sure, let's update your business listing! ✏️\n\n"
        "Please tell me your *business name* or *phone number* "
        "so I can find your listing."
    )


# ── Category picker helper ───────────────────────────────────────
def _category_menu() -> str:
    lines = ["Pick a category (reply with the number):\n"]
    for i, cat in enumerate(VALID_CATEGORIES, 1):
        lines.append(f"{i}. {CATEGORY_DISPLAY[cat]}")
    return "\n".join(lines)


def _parse_category(msg: str) -> str | None:
    """Parse category from number or name."""
    msg = msg.strip().lower()
    # Try number
    try:
        idx = int(msg)
        if 1 <= idx <= len(VALID_CATEGORIES):
            return VALID_CATEGORIES[idx - 1]
    except ValueError:
        pass
    # Try name match
    for cat in VALID_CATEGORIES:
        if cat in msg:
            return cat
    return None


# ── Update field picker ──────────────────────────────────────────
UPDATABLE_FIELDS = ["name", "category", "address", "city", "state", "phone"]

def _field_menu() -> str:
    lines = ["What would you like to update? (reply with the number)\n"]
    labels = {
        "name": "Business Name",
        "category": "Category",
        "address": "Address",
        "city": "City",
        "state": "State",
        "phone": "Phone Number",
    }
    for i, f in enumerate(UPDATABLE_FIELDS, 1):
        lines.append(f"{i}. {labels[f]}")
    return "\n".join(lines)


def _parse_field(msg: str) -> str | None:
    msg = msg.strip().lower()
    try:
        idx = int(msg)
        if 1 <= idx <= len(UPDATABLE_FIELDS):
            return UPDATABLE_FIELDS[idx - 1]
    except ValueError:
        pass
    for f in UPDATABLE_FIELDS:
        if f in msg:
            return f
    return None


# ── Main handler ─────────────────────────────────────────────────
def handle_registration_message(wa_id: str, message: str, settings: Settings) -> str:
    """
    Process a message within an active registration/update session.
    Returns the bot's reply text.
    """
    _clean_expired()
    session = _sessions.get(wa_id)
    if not session:
        return "No active session found. Say *'add my business'* or *'update my business'* to start."

    msg = message.strip()

    # Allow cancel at any point
    if msg.lower() in ("cancel", "stop", "quit", "exit", "nevermind"):
        return cancel_session(wa_id)

    session.updated_at = time.time()

    if session.flow == "add":
        return _handle_add_step(session, msg, settings)
    elif session.flow == "update":
        return _handle_update_step(session, msg, settings)
    else:
        del _sessions[wa_id]
        return "Something went wrong. Please try again."


# ── ADD flow steps ───────────────────────────────────────────────
def _handle_add_step(session: RegistrationSession, msg: str, settings: Settings) -> str:
    step = session.step

    if step == AddStep.NAME:
        session.data["name"] = msg
        session.step = AddStep.CATEGORY
        return f"Got it — *{msg}*\n\n{_category_menu()}"

    elif step == AddStep.CATEGORY:
        cat = _parse_category(msg)
        if not cat:
            return f"I didn't catch that. {_category_menu()}"
        session.data["category"] = cat
        session.step = AddStep.ADDRESS
        return f"Category: *{CATEGORY_DISPLAY[cat]}*\n\nWhat is the *street address*?"

    elif step == AddStep.ADDRESS:
        session.data["address"] = msg
        session.step = AddStep.CITY
        return "What *city* is the business in?"

    elif step == AddStep.CITY:
        session.data["city"] = msg.title()
        session.step = AddStep.STATE
        return f"City: *{msg.title()}*\n\nWhat *state*? (e.g. TX, CA, NJ)"

    elif step == AddStep.STATE:
        state = msg.strip().upper()
        if len(state) > 2:
            # Try to find abbreviation
            from app.services.business_service import STATE_ABBREVS
            state = STATE_ABBREVS.get(msg.lower(), state[:2].upper())
        session.data["state"] = state
        session.step = AddStep.PHONE
        return f"State: *{state}*\n\nWhat is the *phone number*? (or type 'skip' if none)"

    elif step == AddStep.PHONE:
        if msg.lower() == "skip":
            session.data["phone"] = None
        else:
            session.data["phone"] = msg
        session.step = AddStep.CONFIRM
        return _add_confirmation(session)

    elif step == AddStep.CONFIRM:
        if msg.lower() in ("yes", "y", "confirm", "looks good", "correct", "ok", "👍"):
            return _insert_business(session, settings)
        elif msg.lower() in ("no", "n", "restart", "start over"):
            session.step = AddStep.NAME
            session.data = {}
            return "No problem! Let's start over.\n\nWhat is your *business name*?"
        else:
            return "Please reply *yes* to confirm or *no* to start over."

    return "Something went wrong. Type 'cancel' to exit."


def _add_confirmation(session: RegistrationSession) -> str:
    d = session.data
    phone_line = f"📞 {d['phone']}" if d.get("phone") else "📞 (none)"
    return (
        "Here's what I have:\n\n"
        f"🏪 *{d['name']}*\n"
        f"📂 {CATEGORY_DISPLAY.get(d['category'], d['category'])}\n"
        f"📍 {d['address']}, {d['city']}, {d['state']}\n"
        f"{phone_line}\n\n"
        "Does this look correct? Reply *yes* to submit or *no* to start over."
    )


def _insert_business(session: RegistrationSession, settings: Settings) -> str:
    """Insert the new business into Supabase."""
    d = session.data
    try:
        client = create_client(settings.SUPABASE_URL, settings.SUPABASE_KEY)
        row = {
            "id": str(uuid.uuid4()),
            "name": d["name"],
            "category": d["category"],
            "subcategory": d["category"],
            "address": f"{d['address']}, {d['city']}, {d['state']}",
            "city": d["city"],
            "state": d["state"],
            "phone": d.get("phone"),
            "source": "user_submitted",
            "source_id": f"user_{session.wa_id}_{int(time.time())}",
            "is_featured": False,
        }
        client.table("businesses").insert(row).execute()
        del _sessions[session.wa_id]
        logger.info(f"Business added by {session.wa_id}: {d['name']} in {d['city']}, {d['state']}")
        return (
            "Your business has been added to Mira! 🎉🙏\n\n"
            f"*{d['name']}* is now listed and users can find it when they search.\n\n"
            "Want to make changes later? Just say *'update my business'*."
        )
    except Exception as e:
        logger.error(f"Failed to insert business for {session.wa_id}: {e}")
        del _sessions[session.wa_id]
        return "Sorry, something went wrong while saving your business. Please try again later. 🙏"


# ── UPDATE flow steps ────────────────────────────────────────────
def _handle_update_step(session: RegistrationSession, msg: str, settings: Settings) -> str:
    step = session.step

    if step == UpdateStep.LOOKUP:
        return _lookup_business(session, msg, settings)

    elif step == UpdateStep.SELECT:
        return _select_match(session, msg)

    elif step == UpdateStep.CHOOSE_FIELD:
        field = _parse_field(msg)
        if not field:
            return f"I didn't catch that.\n\n{_field_menu()}"
        session.data["update_field"] = field
        session.step = UpdateStep.NEW_VALUE
        if field == "category":
            return f"You want to update the *category*.\n\n{_category_menu()}"
        labels = {
            "name": "business name", "address": "street address",
            "city": "city", "state": "state (e.g. TX)",
            "phone": "phone number",
        }
        return f"Enter the new *{labels.get(field, field)}*:"

    elif step == UpdateStep.NEW_VALUE:
        field = session.data["update_field"]
        if field == "category":
            cat = _parse_category(msg)
            if not cat:
                return f"Invalid category.\n\n{_category_menu()}"
            session.data["new_value"] = cat
        elif field == "state":
            state = msg.strip().upper()
            if len(state) > 2:
                from app.services.business_service import STATE_ABBREVS
                state = STATE_ABBREVS.get(msg.lower(), state[:2].upper())
            session.data["new_value"] = state
        elif field == "city":
            session.data["new_value"] = msg.strip().title()
        else:
            session.data["new_value"] = msg.strip()
        session.step = UpdateStep.CONFIRM
        return _update_confirmation(session)

    elif step == UpdateStep.CONFIRM:
        if msg.lower() in ("yes", "y", "confirm", "looks good", "correct", "ok", "👍"):
            return _update_business(session, settings)
        elif msg.lower() in ("no", "n", "cancel"):
            del _sessions[session.wa_id]
            return "Update cancelled. Let me know if you need anything else! 🙏"
        else:
            return "Please reply *yes* to confirm or *no* to cancel."

    return "Something went wrong. Type 'cancel' to exit."


def _lookup_business(session: RegistrationSession, msg: str, settings: Settings) -> str:
    """Look up a business by name or phone."""
    try:
        client = create_client(settings.SUPABASE_URL, settings.SUPABASE_KEY)
        # Try phone match first
        digits = "".join(c for c in msg if c.isdigit())
        if len(digits) >= 7:
            result = client.table("businesses").select("*").ilike("phone", f"%{digits[-10:]}%").execute()
        else:
            result = client.table("businesses").select("*").ilike("name", f"%{msg}%").execute()

        if not result.data:
            return (
                "I couldn't find a matching business. "
                "Please check the name/phone and try again, or type *cancel* to exit."
            )

        if len(result.data) == 1:
            session.data["business"] = result.data[0]
            session.step = UpdateStep.CHOOSE_FIELD
            b = result.data[0]
            return (
                f"Found: *{b['name']}*\n"
                f"📍 {b.get('address', 'N/A')}\n"
                f"📞 {b.get('phone', 'N/A')}\n\n"
                f"{_field_menu()}"
            )

        # Multiple matches — let user pick
        session.matches = result.data[:5]
        session.step = UpdateStep.SELECT
        lines = ["I found multiple matches. Which one is yours? (reply with the number)\n"]
        for i, b in enumerate(session.matches, 1):
            lines.append(f"{i}. *{b['name']}* — {b.get('city', '')}, {b.get('state', '')}")
        return "\n".join(lines)

    except Exception as e:
        logger.error(f"Business lookup failed for {session.wa_id}: {e}")
        return "Sorry, something went wrong during lookup. Please try again. 🙏"


def _select_match(session: RegistrationSession, msg: str) -> str:
    """User picks from multiple matches."""
    try:
        idx = int(msg.strip()) - 1
        if 0 <= idx < len(session.matches):
            session.data["business"] = session.matches[idx]
            session.step = UpdateStep.CHOOSE_FIELD
            b = session.matches[idx]
            return (
                f"Selected: *{b['name']}*\n\n"
                f"{_field_menu()}"
            )
    except (ValueError, IndexError):
        pass
    return "Please reply with a number from the list, or type *cancel* to exit."


def _update_confirmation(session: RegistrationSession) -> str:
    b = session.data["business"]
    field = session.data["update_field"]
    new_val = session.data["new_value"]
    labels = {
        "name": "Business Name", "category": "Category",
        "address": "Address", "city": "City",
        "state": "State", "phone": "Phone",
    }
    display_val = CATEGORY_DISPLAY.get(new_val, new_val) if field == "category" else new_val
    return (
        f"Update *{b['name']}*:\n\n"
        f"*{labels.get(field, field)}*: {b.get(field, 'N/A')} → *{display_val}*\n\n"
        "Confirm? Reply *yes* or *no*."
    )


def _update_business(session: RegistrationSession, settings: Settings) -> str:
    """Apply the update to Supabase."""
    b = session.data["business"]
    field = session.data["update_field"]
    new_val = session.data["new_value"]

    try:
        client = create_client(settings.SUPABASE_URL, settings.SUPABASE_KEY)

        update_data = {field: new_val}
        # If updating address, city, or state, also update the combined address field
        if field in ("city", "state"):
            update_data[field] = new_val

        client.table("businesses").update(update_data).eq("id", b["id"]).execute()
        del _sessions[session.wa_id]
        logger.info(f"Business updated by {session.wa_id}: {b['name']} — {field} → {new_val}")
        return (
            f"Done! *{b['name']}* has been updated. ✅\n\n"
            "The change is live — users will see it right away.\n"
            "Need anything else? 🙏"
        )
    except Exception as e:
        logger.error(f"Failed to update business for {session.wa_id}: {e}")
        del _sessions[session.wa_id]
        return "Sorry, something went wrong while updating. Please try again later. 🙏"
