"""
Mira — Monetization Service

Handles:
  1. Featured Listings — upgrade/downgrade, Stripe payment link generation
  2. Lead Gen / Inquiry Tracking — log every business view/inquiry
  3. Subscription management — free/featured/premium tiers
  4. Business analytics — show owners their inquiry stats

WhatsApp commands:
  - "feature my business" / "upgrade my business" → Featured listing flow
  - "my stats" / "my analytics" → Show inquiry count for their business
  - "my plan" / "my subscription" → Show current subscription tier
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

# ── Pricing ─────────────────────────────────────────────────────
PLANS = {
    "free": {
        "label": "Free",
        "price": "$0/month",
        "deals_per_month": 1,
        "featured": False,
        "analytics": False,
    },
    "featured": {
        "label": "⭐ Featured",
        "price": "$15/month",
        "deals_per_month": 5,
        "featured": True,
        "analytics": True,
    },
    "premium": {
        "label": "👑 Premium",
        "price": "$30/month",
        "deals_per_month": 999,
        "featured": True,
        "analytics": True,
    },
}


# ── Upgrade flow steps ──────────────────────────────────────────
class UpgradeStep(str, Enum):
    BUSINESS_LOOKUP = "business_lookup"
    SELECT_BUSINESS = "select_business"
    CHOOSE_PLAN = "choose_plan"
    CONFIRM = "confirm"


@dataclass
class UpgradeSession:
    wa_id: str
    step: str
    data: dict = field(default_factory=dict)
    matches: list = field(default_factory=list)
    updated_at: float = field(default_factory=time.time)


_upgrade_sessions: dict[str, UpgradeSession] = {}


def _clean_expired():
    now = time.time()
    expired = [k for k, v in _upgrade_sessions.items() if now - v.updated_at > SESSION_TIMEOUT]
    for k in expired:
        del _upgrade_sessions[k]


def has_active_upgrade_session(wa_id: str) -> bool:
    _clean_expired()
    return wa_id in _upgrade_sessions


# ── Intent detection ────────────────────────────────────────────
def detect_monetization_intent(message: str) -> str | None:
    """
    Detect monetization-related intents.
    Returns "upgrade", "stats", "plan", or None.
    """
    msg = message.lower().strip()

    upgrade_phrases = [
        "feature my business", "upgrade my business", "promote my business",
        "featured listing", "get featured", "upgrade listing",
        "boost my business", "premium listing", "upgrade my plan",
        "i want featured", "make my business featured",
    ]
    stats_phrases = [
        "my stats", "my analytics", "how many views",
        "business stats", "business analytics", "my inquiries",
        "how is my business doing", "my performance",
    ]
    plan_phrases = [
        "my plan", "my subscription", "current plan",
        "what plan", "which plan", "subscription status",
    ]

    for phrase in upgrade_phrases:
        if phrase in msg:
            return "upgrade"
    for phrase in stats_phrases:
        if phrase in msg:
            return "stats"
    for phrase in plan_phrases:
        if phrase in msg:
            return "plan"
    return None


# ── Start upgrade flow ──────────────────────────────────────────
def start_upgrade_flow(wa_id: str) -> str:
    _upgrade_sessions[wa_id] = UpgradeSession(
        wa_id=wa_id,
        step=UpgradeStep.BUSINESS_LOOKUP,
    )
    return (
        "Got you 👍 Let's get you upgraded!\n\n"
        "What's your *business name* or *phone number*?"
    )


def _plan_menu(current_plan: str = "free") -> str:
    lines = ["Choose a plan (reply with the number):\n"]
    i = 1
    for key, p in PLANS.items():
        if key == current_plan:
            lines.append(f"{i}. {p['label']} — {p['price']} ✅ *Current*")
        else:
            extras = []
            if p['featured']:
                extras.append("⭐ Featured badge")
            if p['analytics']:
                extras.append("📊 Analytics")
            extras.append(f"🏷️ {p['deals_per_month']} deals/mo")
            lines.append(f"{i}. {p['label']} — {p['price']}\n   {', '.join(extras)}")
        i += 1
    return "\n".join(lines)


# ── Upgrade session handler ─────────────────────────────────────
def handle_upgrade_message(wa_id: str, message: str, settings: Settings) -> str:
    _clean_expired()
    session = _upgrade_sessions.get(wa_id)
    if not session:
        return "No active session. Say *'feature my business'* to upgrade."

    msg = message.strip()
    if msg.lower() in ("cancel", "stop", "quit", "exit", "nevermind"):
        del _upgrade_sessions[wa_id]
        return "No worries, cancelled 👍"

    session.updated_at = time.time()
    return _handle_upgrade_step(session, msg, settings)


def _handle_upgrade_step(session: UpgradeSession, msg: str, settings: Settings) -> str:
    step = session.step

    if step == UpgradeStep.BUSINESS_LOOKUP:
        return _upgrade_lookup(session, msg, settings)

    elif step == UpgradeStep.SELECT_BUSINESS:
        return _upgrade_select(session, msg)

    elif step == UpgradeStep.CHOOSE_PLAN:
        return _upgrade_choose_plan(session, msg, settings)

    elif step == UpgradeStep.CONFIRM:
        if msg.lower() in ("yes", "y", "confirm", "ok", "👍"):
            return _activate_plan(session, settings)
        elif msg.lower() in ("no", "n", "cancel"):
            del _upgrade_sessions[session.wa_id]
            return "No worries! Your current plan stays active. 🙏"
        return "Reply *yes* to confirm or *no* to cancel."

    return "Something went wrong. Type 'cancel' to exit."


def _upgrade_lookup(session: UpgradeSession, msg: str, settings: Settings) -> str:
    try:
        client = create_client(settings.SUPABASE_URL, settings.SUPABASE_KEY)
        digits = "".join(c for c in msg if c.isdigit())
        if len(digits) >= 7:
            result = client.table("businesses").select("*").ilike("phone", f"%{digits[-10:]}%").execute()
        else:
            result = client.table("businesses").select("*").ilike("name", f"%{msg}%").execute()

        if not result.data:
            return (
                "I couldn't find that business. Check the name/phone, "
                "or *add your business first* by typing 'add my business'.\n"
                "Type *cancel* to exit."
            )

        if len(result.data) == 1:
            session.data["business"] = result.data[0]
            current = _get_current_plan(result.data[0]["id"], settings)
            session.data["current_plan"] = current
            session.step = UpgradeStep.CHOOSE_PLAN
            b = result.data[0]
            featured_badge = " ⭐ Featured" if b.get("is_featured") else ""
            return (
                f"Found: *{b['name']}*{featured_badge}\n"
                f"📍 {b.get('city', '')}, {b.get('state', '')}\n"
                f"Current plan: *{PLANS[current]['label']}*\n\n"
                f"{_plan_menu(current)}"
            )

        session.matches = result.data[:5]
        session.step = UpgradeStep.SELECT_BUSINESS
        lines = ["Multiple matches. Which one? (reply with number)\n"]
        for i, b in enumerate(session.matches, 1):
            lines.append(f"{i}. *{b['name']}* — {b.get('city', '')}, {b.get('state', '')}")
        return "\n".join(lines)

    except Exception as e:
        logger.error(f"Upgrade lookup failed: {e}")
        return "Sorry, something went wrong. Please try again. 🙏"


def _upgrade_select(session: UpgradeSession, msg: str) -> str:
    try:
        idx = int(msg.strip()) - 1
        if 0 <= idx < len(session.matches):
            b = session.matches[idx]
            session.data["business"] = b
            current = "free"  # default
            session.data["current_plan"] = current
            session.step = UpgradeStep.CHOOSE_PLAN
            return (
                f"Selected: *{b['name']}*\n"
                f"Current plan: *{PLANS[current]['label']}*\n\n"
                f"{_plan_menu(current)}"
            )
    except (ValueError, IndexError):
        pass
    return "Please reply with a number from the list, or *cancel* to exit."


def _upgrade_choose_plan(session: UpgradeSession, msg: str, settings: Settings) -> str:
    plan_keys = list(PLANS.keys())
    try:
        idx = int(msg.strip()) - 1
        if 0 <= idx < len(plan_keys):
            chosen = plan_keys[idx]
            if chosen == session.data.get("current_plan"):
                return "That's already your current plan! Pick a different one, or *cancel*."
            if chosen == "free":
                session.data["chosen_plan"] = "free"
                session.step = UpgradeStep.CONFIRM
                return (
                    "Switch to *Free* plan?\n"
                    "You'll lose your Featured badge + limited to 1 deal/month.\n\n"
                    "Reply *yes* to confirm or *no* to cancel."
                )
            session.data["chosen_plan"] = chosen
            session.step = UpgradeStep.CONFIRM
            p = PLANS[chosen]
            return (
                f"*{p['label']}* — {p['price']}\n\n"
                f"⭐ Appear first in search\n"
                f"🏷️ {p['deals_per_month']} deals/month\n"
                f"📊 Analytics & inquiry stats\n\n"
                "Reply *yes* to activate or *no* to cancel."
            )
    except (ValueError, IndexError):
        pass
    return f"Please pick a number.\n\n{_plan_menu(session.data.get('current_plan', 'free'))}"


def _activate_plan(session: UpgradeSession, settings: Settings) -> str:
    """Activate the chosen plan in Supabase."""
    b = session.data["business"]
    chosen = session.data["chosen_plan"]
    p = PLANS[chosen]

    try:
        client = create_client(settings.SUPABASE_URL, settings.SUPABASE_KEY)

        # Update business featured status
        client.table("businesses").update({
            "is_featured": p["featured"]
        }).eq("id", b["id"]).execute()

        # Upsert subscription record
        now = datetime.now(timezone.utc)
        sub_row = {
            "id": str(uuid.uuid4()),
            "business_id": b["id"],
            "wa_id": session.wa_id,
            "plan": chosen,
            "status": "active",
            "deals_per_month": p["deals_per_month"],
            "starts_at": now.isoformat(),
            "expires_at": (now + timedelta(days=30)).isoformat() if chosen != "free" else None,
        }
        # Delete old subscription for this business first
        client.table("subscriptions").delete().eq("business_id", b["id"]).execute()
        client.table("subscriptions").insert(sub_row).execute()

        del _upgrade_sessions[session.wa_id]
        logger.info(f"Plan activated: {b['name']} → {chosen} by {session.wa_id}")

        if chosen == "free":
            return (
                "Done! Your listing is back on the *Free* plan. ✅\n\n"
                "Want to upgrade again anytime? Just say *'feature my business'*."
            )

        # For paid plans, send payment instructions with Stripe link
        stripe_links = {
            "featured": settings.STRIPE_FEATURED_LINK,
            "premium": settings.STRIPE_PREMIUM_LINK,
        }
        payment_link = stripe_links.get(chosen, "")

        msg = (
            f"*{b['name']}* is now *{p['label']}*! 🎉\n\n"
            f"⭐ Featured in search\n"
            f"🏷️ {p['deals_per_month']} deals/month\n"
            f"📊 Type *'my stats'* for analytics\n\n"
        )

        if payment_link:
            msg += (
                f"💳 Complete payment ({p['price']}):\n"
                f"{payment_link}\n"
            )
        else:
            msg += (
                f"💳 Payment link ({p['price']}) coming shortly\n"
            )

        msg += "\nYou're live immediately 👍"
        return msg

    except Exception as e:
        logger.error(f"Failed to activate plan: {e}")
        del _upgrade_sessions[session.wa_id]
        return "Sorry, something went wrong. Please try again later. 🙏"


# ── Helper: get current plan ────────────────────────────────────
def _get_current_plan(business_id: str, settings: Settings) -> str:
    try:
        client = create_client(settings.SUPABASE_URL, settings.SUPABASE_KEY)
        result = (
            client.table("subscriptions")
            .select("plan")
            .eq("business_id", business_id)
            .eq("status", "active")
            .limit(1)
            .execute()
        )
        if result.data:
            return result.data[0]["plan"]
    except Exception:
        pass
    return "free"


# ══════════════════════════════════════════════════════════════════
# LEAD GEN / INQUIRY TRACKING
# ══════════════════════════════════════════════════════════════════

def log_inquiry(
    businesses: list[dict],
    user_wa_id: str,
    inquiry_type: str,
    message_snippet: str,
    settings: Settings,
) -> None:
    """Log inquiries for each business shown to a user."""
    if not businesses:
        return
    try:
        client = create_client(settings.SUPABASE_URL, settings.SUPABASE_KEY)
        rows = []
        for b in businesses:
            rows.append({
                "id": str(uuid.uuid4()),
                "business_id": b.get("id"),
                "business_name": b.get("name", "Unknown"),
                "user_wa_id": user_wa_id,
                "inquiry_type": inquiry_type,
                "message_snippet": message_snippet[:100] if message_snippet else "",
                "city": b.get("city"),
                "state": b.get("state"),
            })
        if rows:
            client.table("inquiry_logs").insert(rows).execute()
            logger.info(f"Logged {len(rows)} inquiries for user {user_wa_id}")

        # ── Instant lead notifications to business owners ──
        _notify_business_owners(businesses, message_snippet, settings)

    except Exception as e:
        logger.warning(f"Failed to log inquiries: {e}")


def _notify_business_owners(
    businesses: list[dict],
    search_query: str,
    settings: Settings,
) -> None:
    """
    Send instant WhatsApp notification to business owners when someone
    searches for their business. This is the dopamine hit that drives upgrades.

    Only notifies businesses that have a source_id (owner's wa_id).
    Rate-limited: max 1 notification per business per hour to avoid spam.
    """
    import asyncio

    for b in businesses:
        try:
            source_id = (b.get("source_id") or "").strip()
            if not source_id:
                continue  # No owner linked

            owner_wa_id = source_id.replace("wa:", "").strip()
            if not owner_wa_id:
                continue

            biz_name = b.get("name", "your business")
            city = b.get("city", "")
            is_featured = b.get("is_featured", False)

            # Rate limit: check last notification time (simple in-memory)
            cache_key = f"notif:{b.get('id', '')}"
            now = time.time()
            last_sent = _notification_cache.get(cache_key, 0)
            if now - last_sent < 3600:  # 1 hour cooldown
                continue

            _notification_cache[cache_key] = now

            # Build notification message — Mira voice
            query_preview = search_query[:60] if search_query else "a local service"
            msg = (
                f"🔔 *New customer interest!*\n\n"
                f"Someone searched for:\n"
                f"👉 _{query_preview}_\n\n"
                f"Your business *{biz_name}* was shown"
            )
            if city:
                msg += f" in {city}"
            msg += " 👍\n"

            if not is_featured:
                msg += (
                    "\n👉 Upgrade to appear first\n"
                    "Reply *\"upgrade\"* to activate"
                )
            else:
                msg += "\n✅ Your Featured badge helped you appear first!"

            # Send async notification (fire-and-forget)
            from app.services.whatsapp_service import WhatsAppService
            whatsapp = WhatsAppService(settings)

            # Use asyncio to send without blocking
            try:
                loop = asyncio.get_running_loop()
                loop.create_task(whatsapp.send_text_message(owner_wa_id, msg))
            except RuntimeError:
                # No running loop — skip (will happen in sync contexts)
                pass

            logger.info(f"Lead notification sent: {biz_name} → {owner_wa_id}")

        except Exception as e:
            logger.warning(f"Lead notification failed for {b.get('name', '?')}: {e}")


# In-memory rate limiter for lead notifications (1 per business per hour)
_notification_cache: dict[str, float] = {}


# ── Business stats (for owners) ─────────────────────────────────
def get_business_stats(wa_id: str, settings: Settings) -> str:
    """Show inquiry stats for businesses owned by this wa_id."""
    try:
        client = create_client(settings.SUPABASE_URL, settings.SUPABASE_KEY)

        # Find businesses submitted by this user
        biz_result = (
            client.table("businesses")
            .select("id, name, city, state, is_featured")
            .ilike("source_id", f"%{wa_id}%")
            .execute()
        )

        if not biz_result.data:
            return (
                "I couldn't find any businesses linked to your account.\n"
                "Add your business first by typing *'add my business'*."
            )

        lines = ["📊 *Your Business Analytics*\n"]
        for b in biz_result.data:
            featured = " ⭐" if b.get("is_featured") else ""
            # Count inquiries in last 30 days
            thirty_days_ago = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()
            stats = (
                client.table("inquiry_logs")
                .select("inquiry_type", count="exact")
                .eq("business_id", b["id"])
                .gte("created_at", thirty_days_ago)
                .execute()
            )

            count = stats.count if stats.count else 0
            lines.append(
                f"🏪 *{b['name']}*{featured}\n"
                f"   📍 {b.get('city', '')}, {b.get('state', '')}\n"
                f"   👀 {count} inquiries in last 30 days\n"
            )

        if any(not b.get("is_featured") for b in biz_result.data):
            lines.append(
                "👉 Want more leads? Reply *\"upgrade\"* for Featured placement"
            )

        return "\n".join(lines)

    except Exception as e:
        logger.error(f"Failed to get stats for {wa_id}: {e}")
        return "Sorry, couldn't load your stats right now. Try again later. 🙏"


def get_plan_status(wa_id: str, settings: Settings) -> str:
    """Show current subscription plan for this user."""
    try:
        client = create_client(settings.SUPABASE_URL, settings.SUPABASE_KEY)
        result = (
            client.table("subscriptions")
            .select("*, businesses(name, city, state)")
            .eq("wa_id", wa_id)
            .eq("status", "active")
            .execute()
        )

        if not result.data:
            return (
                "You're on the *Free* plan.\n\n"
                "👉 Reply *\"upgrade\"* to get featured + analytics"
            )

        lines = ["📋 *Your Subscription*\n"]
        for sub in result.data:
            p = PLANS.get(sub["plan"], PLANS["free"])
            biz = sub.get("businesses", {})
            biz_name = biz.get("name", "Unknown") if biz else "Unknown"
            expires = sub.get("expires_at", "")
            expire_str = ""
            if expires:
                try:
                    exp_dt = datetime.fromisoformat(expires.replace("Z", "+00:00"))
                    days_left = (exp_dt - datetime.now(timezone.utc)).days
                    expire_str = f"⏰ {days_left} days remaining"
                except Exception:
                    pass
            lines.append(
                f"🏪 *{biz_name}*\n"
                f"   Plan: {p['label']} ({p['price']})\n"
                f"   🏷️ {p['deals_per_month']} deals/month\n"
                f"   {expire_str}\n"
            )

        return "\n".join(lines)

    except Exception as e:
        logger.error(f"Failed to get plan status for {wa_id}: {e}")
        return "Sorry, couldn't load your subscription. Try again later. 🙏"
