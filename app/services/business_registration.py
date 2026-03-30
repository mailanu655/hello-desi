"""
Mira — Business Registration & Update Service (v2 — hardened)

Handles multi-step WhatsApp conversations for:
  1. Adding a new business listing
  2. Updating an existing business listing

v2 improvements:
- Ownership verification on updates (only owner can edit)
- Duplicate business detection before insert
- Phone number validation (10-digit)
- State abbreviation validation (no silent truncation)
- Address/city/state consistency on updates
- Session preserved on transient DB errors (retry-friendly)
- "back" command to return to previous step
- Progress indicators (Step X/Y)
- Expanded confirmation vocabulary (haan, ji, sure, yep)
- Category list synced with search layer (17 categories)
- Upgrade CTA at peak intent after registration

Uses Redis-backed session store (survives container restarts).
Falls back to in-memory if Redis is unavailable.
"""

import logging
import re
import time
import uuid
from enum import Enum

from supabase import create_client
from config.settings import Settings
from app.services.session_store import get_session, set_session, delete_session, session_exists

logger = logging.getLogger(__name__)

# Redis key prefix for registration sessions
_KEY_PREFIX = "reg:"

# ── Valid categories (synced with business_service.CATEGORY_MAP values) ──
VALID_CATEGORIES = [
    "restaurant", "grocery", "temple", "doctor", "lawyer",
    "cpa", "realtor", "travel", "insurance", "salon",
    "jeweler", "banquet", "childcare", "tutor", "driving",
    "cleaning", "photographer",
]

CATEGORY_DISPLAY = {
    "restaurant": "🍛 Restaurant / Food",
    "grocery": "🛒 Grocery Store",
    "temple": "🛕 Temple / Place of Worship",
    "doctor": "🩺 Doctor / Medical",
    "lawyer": "⚖️ Lawyer / Attorney",
    "cpa": "📊 CPA / Accountant",
    "realtor": "🏠 Realtor / Real Estate",
    "travel": "✈️ Travel Agent",
    "insurance": "🛡️ Insurance Agent",
    "salon": "💇 Salon / Beauty / Spa",
    "jeweler": "💎 Jeweler",
    "banquet": "🎉 Banquet / Event Venue",
    "childcare": "👶 Childcare / Nanny / Daycare",
    "tutor": "📚 Tutor / Coaching",
    "driving": "🚗 Driving School",
    "cleaning": "🧹 Cleaning / Housekeeping",
    "photographer": "📸 Photographer / Videographer",
}

# ── Confirmation vocabulary (includes Hindi affirmatives) ──────
YES_WORDS = {
    "yes", "y", "confirm", "looks good", "correct", "ok",
    "👍", "sure", "yep", "yeah", "yea", "haan", "ji",
    "ha", "sahi hai", "theek hai", "done",
}
NO_WORDS = {"no", "n", "restart", "start over", "nahi", "nah"}

# ── Add flow: step order for back navigation ──────────────────
ADD_STEP_ORDER = ["name", "category", "address", "city", "state", "phone", "confirm"]
ADD_TOTAL_STEPS = 7


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


def _key(wa_id: str) -> str:
    return f"{_KEY_PREFIX}{wa_id}"


def _get(wa_id: str, settings: Settings) -> dict | None:
    """Get the current registration session for a user."""
    return get_session(_key(wa_id), settings)


def _save(wa_id: str, session: dict, settings: Settings) -> None:
    """Save/update a registration session."""
    set_session(_key(wa_id), session, settings)


def _delete(wa_id: str, settings: Settings) -> None:
    """Delete a registration session."""
    delete_session(_key(wa_id), settings)


def has_active_session(wa_id: str, settings: Settings | None = None) -> bool:
    """Check if user has an active registration/update session."""
    if settings is None:
        from config.settings import get_settings
        settings = get_settings()
    return session_exists(_key(wa_id), settings)


def cancel_session(wa_id: str, settings: Settings | None = None) -> str:
    """Cancel an active session."""
    if settings is None:
        from config.settings import get_settings
        settings = get_settings()
    _delete(wa_id, settings)
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


# ── Registration rate limit (prevents spam listings) ─────────────
MAX_REGISTRATIONS_PER_DAY = 3

def _check_registration_limit(wa_id: str, settings: Settings) -> bool:
    """
    Check if user has exceeded daily registration limit.
    Returns True if WITHIN limits, False if rate-limited.
    """
    try:
        from app.services.session_store import _get_redis
        r = _get_redis(settings)
        if not r:
            return True  # No Redis = no limit
        key = f"reg_count:{wa_id}"
        count = r.incr(key)
        if count == 1:
            r.expire(key, 86400)  # 24-hour window
        return count <= MAX_REGISTRATIONS_PER_DAY
    except Exception as e:
        logger.warning(f"Registration rate limit check failed: {e}")
        return True  # On failure, allow


# ── Start flows ──────────────────────────────────────────────────
def start_add_flow(wa_id: str, settings: Settings | None = None) -> str:
    """Begin the 'add my business' conversation."""
    if settings is None:
        from config.settings import get_settings
        settings = get_settings()

    # Check registration rate limit
    if not _check_registration_limit(wa_id, settings):
        return (
            "You've reached the daily limit for adding businesses (3 per day).\n"
            "Please try again tomorrow, or type *\"update my business\"* to edit an existing listing. 🙏"
        )

    _save(wa_id, {
        "wa_id": wa_id,
        "flow": "add",
        "step": AddStep.NAME,
        "data": {},
        "matches": [],
    }, settings)
    return (
        "Got you 👍 Let's get you listed!\n\n"
        "I'll ask a few quick questions (7 steps).\n"
        "Type *back* anytime to fix the previous answer.\n\n"
        "*Step 1/7* — What is your *business name*?"
    )


def start_update_flow(wa_id: str, settings: Settings | None = None) -> str:
    """Begin the 'update my business' conversation."""
    if settings is None:
        from config.settings import get_settings
        settings = get_settings()
    _save(wa_id, {
        "wa_id": wa_id,
        "flow": "update",
        "step": UpdateStep.LOOKUP,
        "data": {},
        "matches": [],
    }, settings)
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


# ── Phone validation ─────────────────────────────────────────────
def _validate_phone(msg: str) -> str | None:
    """
    Extract and validate a 10-digit US phone number.
    Returns cleaned 10-digit string or None if invalid.
    """
    digits = re.sub(r"\D", "", msg)
    # Handle +1 prefix
    if len(digits) == 11 and digits.startswith("1"):
        digits = digits[1:]
    if len(digits) == 10:
        return digits
    return None


# ── State validation ─────────────────────────────────────────────
def _validate_state(msg: str) -> str | None:
    """
    Validate and normalize a US state input.
    Returns 2-letter abbreviation or None if unrecognized.
    """
    from app.services.business_service import STATE_ABBREVS
    cleaned = msg.strip()

    # Already a 2-letter abbreviation?
    if len(cleaned) == 2:
        upper = cleaned.upper()
        if upper in STATE_ABBREVS.values():
            return upper
        return None

    # Try full name lookup
    lower = cleaned.lower()
    if lower in STATE_ABBREVS:
        return STATE_ABBREVS[lower]

    return None


# ── Back navigation helper ───────────────────────────────────────
def _go_back_add(session: dict, settings: Settings) -> str:
    """Move to the previous step in the add flow."""
    wa_id = session["wa_id"]
    current_step = session["step"]
    try:
        current_idx = ADD_STEP_ORDER.index(current_step)
    except ValueError:
        current_idx = 0

    if current_idx == 0:
        return "You're already at the first step.\n\n*Step 1/7* — What is your *business name*?"

    prev_step = ADD_STEP_ORDER[current_idx - 1]
    session["step"] = prev_step
    _save(wa_id, session, settings)

    step_num = current_idx  # prev step's 1-indexed position
    prompts = {
        "name": f"*Step 1/{ADD_TOTAL_STEPS}* — What is your *business name*?",
        "category": f"*Step 2/{ADD_TOTAL_STEPS}* — {_category_menu()}",
        "address": f"*Step 3/{ADD_TOTAL_STEPS}* — What is the *street address*?",
        "city": f"*Step 4/{ADD_TOTAL_STEPS}* — What *city* is the business in?",
        "state": f"*Step 5/{ADD_TOTAL_STEPS}* — What *state*? (e.g. TX, CA, NJ)",
        "phone": f"*Step 6/{ADD_TOTAL_STEPS}* — What is the *phone number*? (or type 'skip')",
    }
    return f"Going back 👍\n\n{prompts.get(prev_step, 'Please continue.')}"


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


# ── Ownership check ──────────────────────────────────────────────
def _is_owner(business: dict, wa_id: str) -> bool:
    """Check if the caller owns this business listing (exact prefix match)."""
    source_id = business.get("source_id", "")
    return source_id.startswith(f"user_{wa_id}_")


# ── Lightweight audit log ────────────────────────────────────────
def _log_event(event: str, wa_id: str, details: dict, settings: Settings) -> None:
    """Log a registration/update event for debugging and trust auditing."""
    try:
        client = create_client(settings.SUPABASE_URL, settings.SUPABASE_KEY)
        client.table("notification_log").insert({
            "id": str(uuid.uuid4()),
            "business_id": details.get("business_id", ""),
            "business_name": details.get("business_name", ""),
            "owner_wa_id": wa_id,
            "search_query": "",
            "status": event,
            "error_msg": str(details)[:500],
        }).execute()
    except Exception as e:
        # Audit log is best-effort — never block the flow
        logger.warning(f"Audit log failed for {event}: {e}")


# ── Main handler ─────────────────────────────────────────────────
def handle_registration_message(wa_id: str, message: str, settings: Settings) -> str:
    """
    Process a message within an active registration/update session.
    Returns the bot's reply text.
    """
    session = _get(wa_id, settings)
    if not session:
        return "No active session found. Say *'add my business'* or *'update my business'* to start."

    msg = message.strip()

    # Allow cancel at any point
    if msg.lower() in ("cancel", "stop", "quit", "exit", "nevermind"):
        return cancel_session(wa_id, settings)

    # Allow "back" in add flow (not at first step)
    if msg.lower() == "back" and session["flow"] == "add":
        return _go_back_add(session, settings)

    if session["flow"] == "add":
        return _handle_add_step(session, msg, settings)
    elif session["flow"] == "update":
        return _handle_update_step(session, msg, settings)
    else:
        _delete(wa_id, settings)
        return "Something went wrong. Please try again."


# ── ADD flow steps ───────────────────────────────────────────────
def _handle_add_step(session: dict, msg: str, settings: Settings) -> str:
    step = session["step"]
    wa_id = session["wa_id"]
    data = session.get("data", {})

    if step == AddStep.NAME:
        # Basic name validation
        if len(msg) < 2:
            return "*Step 1/7* — Please enter a valid business name (at least 2 characters)."
        if len(msg) > 100:
            return "*Step 1/7* — Business name is too long. Please keep it under 100 characters."
        data["name"] = msg
        session["data"] = data
        session["step"] = AddStep.CATEGORY
        _save(wa_id, session, settings)
        return f"Got it — *{msg}*\n\n*Step 2/{ADD_TOTAL_STEPS}* — {_category_menu()}"

    elif step == AddStep.CATEGORY:
        cat = _parse_category(msg)
        if not cat:
            return f"I didn't catch that.\n\n{_category_menu()}"
        data["category"] = cat
        session["data"] = data
        session["step"] = AddStep.ADDRESS
        _save(wa_id, session, settings)
        return (
            f"Category: *{CATEGORY_DISPLAY[cat]}*\n\n"
            f"*Step 3/{ADD_TOTAL_STEPS}* — What is the *street address*?"
        )

    elif step == AddStep.ADDRESS:
        data["address"] = msg
        session["data"] = data
        session["step"] = AddStep.CITY
        _save(wa_id, session, settings)
        return f"*Step 4/{ADD_TOTAL_STEPS}* — What *city* is the business in?"

    elif step == AddStep.CITY:
        city = msg.strip().title()
        data["city"] = city
        session["data"] = data
        session["step"] = AddStep.STATE
        _save(wa_id, session, settings)
        return (
            f"City: *{city}*\n\n"
            f"*Step 5/{ADD_TOTAL_STEPS}* — What *state*? (e.g. TX, CA, NJ)"
        )

    elif step == AddStep.STATE:
        state = _validate_state(msg)
        if not state:
            return (
                "I didn't recognize that state. "
                "Please enter a valid US state name (e.g. *Texas*) or abbreviation (e.g. *TX*)."
            )
        data["state"] = state
        session["data"] = data
        session["step"] = AddStep.PHONE
        _save(wa_id, session, settings)
        return (
            f"State: *{state}*\n\n"
            f"*Step 6/{ADD_TOTAL_STEPS}* — What is the *phone number*? (or type 'skip' if none)"
        )

    elif step == AddStep.PHONE:
        if msg.lower() == "skip":
            data["phone"] = None
        else:
            phone = _validate_phone(msg)
            if not phone:
                return (
                    "Please enter a valid 10-digit phone number (e.g. *6145551234*), "
                    "or type *skip* if you don't have one."
                )
            data["phone"] = phone
        session["data"] = data
        session["step"] = AddStep.CONFIRM
        _save(wa_id, session, settings)
        return _add_confirmation(data)

    elif step == AddStep.CONFIRM:
        if msg.lower() in YES_WORDS:
            # If duplicate was already flagged and user insists, skip re-check
            if data.get("_duplicate_warned"):
                return _do_insert(session, settings)
            return _insert_business(session, settings)
        elif msg.lower() in NO_WORDS:
            session["step"] = AddStep.NAME
            session["data"] = {}
            _save(wa_id, session, settings)
            return f"No problem! Let's start over.\n\n*Step 1/{ADD_TOTAL_STEPS}* — What is your *business name*?"
        else:
            return "Please reply *yes* to confirm or *no* to start over."

    return "Something went wrong. Type 'cancel' to exit."


def _add_confirmation(d: dict) -> str:
    phone_line = f"📞 {d['phone']}" if d.get("phone") else "📞 (none)"
    return (
        f"*Step 7/{ADD_TOTAL_STEPS}* — Here's what I have:\n\n"
        f"🏪 *{d['name']}*\n"
        f"📂 {CATEGORY_DISPLAY.get(d['category'], d['category'])}\n"
        f"📍 {d['address']}, {d['city']}, {d['state']}\n"
        f"{phone_line}\n\n"
        "Does this look correct? Reply *yes* to submit or *no* to start over."
    )


def _check_duplicate(name: str, city: str, settings: Settings) -> bool:
    """
    Check if a business with similar name already exists in the same city.
    Uses first 10 chars of name to catch spelling variations.
    """
    try:
        client = create_client(settings.SUPABASE_URL, settings.SUPABASE_KEY)
        # Use first 10 chars to catch close duplicates ("Taj Palace" vs "Taj Palace Restaurant")
        name_prefix = name[:10].strip() if len(name) >= 10 else name
        result = (
            client.table("businesses")
            .select("id, name")
            .ilike("name", f"%{name_prefix}%")
            .ilike("city", city)
            .limit(1)
            .execute()
        )
        return bool(result.data)
    except Exception as e:
        logger.warning(f"Duplicate check failed: {e}")
        return False  # Don't block registration on check failure


def _insert_business(session: dict, settings: Settings) -> str:
    """Insert the new business into Supabase (with duplicate check)."""
    d = session["data"]
    wa_id = session["wa_id"]

    # ── Duplicate check ──
    if _check_duplicate(d["name"], d["city"], settings):
        d["_duplicate_warned"] = True
        session["data"] = d
        _save(wa_id, session, settings)
        return (
            f"A business named *{d['name']}* already exists in *{d['city']}*.\n\n"
            "If this is your business, try *\"update my business\"* instead.\n"
            "If it's a different business, please use a more specific name.\n\n"
            "Reply *yes* to list anyway, or *no* to cancel."
        )

    return _do_insert(session, settings)


def _do_insert(session: dict, settings: Settings) -> str:
    """Actually perform the Supabase insert."""
    d = session["data"]
    wa_id = session["wa_id"]
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
            "source_id": f"user_{wa_id}_{int(time.time())}",
            "is_featured": False,
        }
        client.table("businesses").insert(row).execute()
        _delete(wa_id, settings)
        logger.info(f"Business added by {wa_id}: {d['name']} in {d['city']}, {d['state']}")
        _log_event("business_added", wa_id, {
            "business_id": row["id"],
            "business_name": d["name"],
            "city": d["city"],
            "state": d["state"],
            "category": d["category"],
        }, settings)
        return (
            f"*{d['name']}* is now listed! 🎉\n\n"
            "People can find you when they search 👍\n\n"
            "🚀 *Post your first deal* to attract customers!\n"
            "👉 Reply *\"post a deal\"*\n\n"
            "Want to appear at the *top* when people search?\n"
            "⭐ *Featured* — $15/month\n"
            "🚀 *Premium* — $30/month\n"
            "👉 Type *\"upgrade\"* to activate\n\n"
            "Want to make changes later? Just say *\"update my business\"*\n"
            "Want daily updates? Try *\"daily digest in [your city]\"*"
        )
    except Exception as e:
        logger.error(f"Failed to insert business for {wa_id}: {e}")
        # ── Keep session alive for retry ──
        return (
            "Sorry, something went wrong while saving. "
            "Please reply *yes* to try again, or *cancel* to exit. 🙏"
        )


# ── UPDATE flow steps ────────────────────────────────────────────
def _handle_update_step(session: dict, msg: str, settings: Settings) -> str:
    step = session["step"]
    wa_id = session["wa_id"]
    data = session.get("data", {})

    if step == UpdateStep.LOOKUP:
        return _lookup_business(session, msg, settings)

    elif step == UpdateStep.SELECT:
        return _select_match(session, msg, settings)

    elif step == UpdateStep.CHOOSE_FIELD:
        fld = _parse_field(msg)
        if not fld:
            return f"I didn't catch that.\n\n{_field_menu()}"
        data["update_field"] = fld
        session["data"] = data
        session["step"] = UpdateStep.NEW_VALUE
        _save(wa_id, session, settings)
        if fld == "category":
            return f"You want to update the *category*.\n\n{_category_menu()}"
        labels = {
            "name": "business name", "address": "street address",
            "city": "city", "state": "state (e.g. TX)",
            "phone": "phone number",
        }
        return f"Enter the new *{labels.get(fld, fld)}*:"

    elif step == UpdateStep.NEW_VALUE:
        fld = data["update_field"]
        if fld == "category":
            cat = _parse_category(msg)
            if not cat:
                return f"Invalid category.\n\n{_category_menu()}"
            data["new_value"] = cat
        elif fld == "state":
            state = _validate_state(msg)
            if not state:
                return (
                    "I didn't recognize that state. "
                    "Please enter a valid US state name (e.g. *Texas*) or abbreviation (e.g. *TX*)."
                )
            data["new_value"] = state
        elif fld == "city":
            data["new_value"] = msg.strip().title()
        elif fld == "phone":
            phone = _validate_phone(msg)
            if not phone:
                return "Please enter a valid 10-digit phone number (e.g. *6145551234*)."
            data["new_value"] = phone
        else:
            data["new_value"] = msg.strip()
        session["data"] = data
        session["step"] = UpdateStep.CONFIRM
        _save(wa_id, session, settings)
        return _update_confirmation(data)

    elif step == UpdateStep.CONFIRM:
        if msg.lower() in YES_WORDS:
            return _update_business(session, settings)
        elif msg.lower() in NO_WORDS or msg.lower() == "cancel":
            _delete(wa_id, settings)
            return "Update cancelled. Let me know if you need anything else! 🙏"
        else:
            return "Please reply *yes* to confirm or *no* to cancel."

    return "Something went wrong. Type 'cancel' to exit."


def _lookup_business(session: dict, msg: str, settings: Settings) -> str:
    """Look up a business by name or phone — scoped to owner's listings."""
    wa_id = session["wa_id"]
    data = session.get("data", {})
    try:
        client = create_client(settings.SUPABASE_URL, settings.SUPABASE_KEY)
        digits = "".join(c for c in msg if c.isdigit())

        # Ownership filter: exact prefix match (not ILIKE partial)
        owner_prefix = f"user_{wa_id}_"

        if len(digits) >= 7:
            result = (
                client.table("businesses")
                .select("*")
                .ilike("phone", f"%{digits[-10:]}%")
                .ilike("source_id", f"{owner_prefix}%")
                .limit(10)
                .execute()
            )
        else:
            result = (
                client.table("businesses")
                .select("*")
                .ilike("name", f"%{msg}%")
                .ilike("source_id", f"{owner_prefix}%")
                .limit(10)
                .execute()
            )

        if not result.data:
            # Try broader search without ownership filter, but mark non-owned
            if len(digits) >= 7:
                broad = client.table("businesses").select("*").ilike("phone", f"%{digits[-10:]}%").limit(5).execute()
            else:
                broad = client.table("businesses").select("*").ilike("name", f"%{msg}%").limit(5).execute()

            if broad.data:
                # Found businesses but user doesn't own them
                return (
                    "I found matching businesses, but they weren't added from your account.\n\n"
                    "You can only update businesses you registered.\n"
                    "Want to *add your own listing* instead? Type *cancel* then *\"add my business\"*."
                )

            return (
                "I couldn't find a matching business. "
                "Please check the name/phone and try again, or type *cancel* to exit."
            )

        if len(result.data) == 1:
            data["business"] = result.data[0]
            session["data"] = data
            session["step"] = UpdateStep.CHOOSE_FIELD
            _save(wa_id, session, settings)
            b = result.data[0]
            return (
                f"Found: *{b['name']}*\n"
                f"📍 {b.get('address', 'N/A')}\n"
                f"📞 {b.get('phone', 'N/A')}\n\n"
                f"{_field_menu()}"
            )

        # Multiple matches
        session["matches"] = result.data[:5]
        session["step"] = UpdateStep.SELECT
        _save(wa_id, session, settings)
        lines = ["I found multiple matches. Which one is yours? (reply with the number)\n"]
        for i, b in enumerate(result.data[:5], 1):
            lines.append(f"{i}. *{b['name']}* — {b.get('city', '')}, {b.get('state', '')}")
        return "\n".join(lines)

    except Exception as e:
        logger.error(f"Business lookup failed for {wa_id}: {e}")
        return "Sorry, something went wrong during lookup. Please try again. 🙏"


def _select_match(session: dict, msg: str, settings: Settings) -> str:
    """User picks from multiple matches."""
    wa_id = session["wa_id"]
    matches = session.get("matches", [])
    data = session.get("data", {})
    try:
        idx = int(msg.strip()) - 1
        if 0 <= idx < len(matches):
            data["business"] = matches[idx]
            session["data"] = data
            session["step"] = UpdateStep.CHOOSE_FIELD
            _save(wa_id, session, settings)
            b = matches[idx]
            return (
                f"Selected: *{b['name']}*\n\n"
                f"{_field_menu()}"
            )
    except (ValueError, IndexError):
        pass
    return "Please reply with a number from the list, or type *cancel* to exit."


def _update_confirmation(data: dict) -> str:
    b = data["business"]
    fld = data["update_field"]
    new_val = data["new_value"]
    labels = {
        "name": "Business Name", "category": "Category",
        "address": "Address", "city": "City",
        "state": "State", "phone": "Phone",
    }
    display_val = CATEGORY_DISPLAY.get(new_val, new_val) if fld == "category" else new_val
    return (
        f"Update *{b['name']}*:\n\n"
        f"*{labels.get(fld, fld)}*: {b.get(fld, 'N/A')} → *{display_val}*\n\n"
        "Confirm? Reply *yes* or *no*."
    )


def _update_business(session: dict, settings: Settings) -> str:
    """Apply the update to Supabase — with address consistency."""
    data = session["data"]
    wa_id = session["wa_id"]
    b = data["business"]
    fld = data["update_field"]
    new_val = data["new_value"]

    try:
        client = create_client(settings.SUPABASE_URL, settings.SUPABASE_KEY)

        update_data = {fld: new_val}

        # ── Address consistency: rebuild compound address field ──
        if fld == "city":
            # Update city column AND rebuild address
            street = b.get("address", "").split(",")[0].strip() if b.get("address") else ""
            state = b.get("state", "")
            update_data["address"] = f"{street}, {new_val}, {state}"
        elif fld == "state":
            street = b.get("address", "").split(",")[0].strip() if b.get("address") else ""
            city = b.get("city", "")
            update_data["address"] = f"{street}, {city}, {new_val}"
        elif fld == "address":
            # User is updating the street portion — rebuild full address
            city = b.get("city", "")
            state = b.get("state", "")
            update_data["address"] = f"{new_val}, {city}, {state}"

        client.table("businesses").update(update_data).eq("id", b["id"]).execute()
        _delete(wa_id, settings)
        logger.info(f"Business updated by {wa_id}: {b['name']} — {fld} → {new_val}")
        _log_event("business_updated", wa_id, {
            "business_id": b["id"],
            "business_name": b["name"],
            "field": fld,
            "old_value": b.get(fld, ""),
            "new_value": new_val,
        }, settings)
        return (
            f"Done! *{b['name']}* has been updated. ✅\n\n"
            "The change is live — users will see it right away.\n"
            "Need anything else? 🙏"
        )
    except Exception as e:
        logger.error(f"Failed to update business for {wa_id}: {e}")
        # ── Keep session alive for retry ──
        return (
            "Sorry, something went wrong while updating. "
            "Please reply *yes* to try again, or *cancel* to exit. 🙏"
        )
