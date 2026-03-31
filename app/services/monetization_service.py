"""
Mira — Monetization Service (v2 — hardened)

Handles:
  1. Featured Listings — upgrade/downgrade, Stripe payment link generation
  2. Lead Gen / Inquiry Tracking — log every business view with position attribution
  3. Subscription management — free/featured/premium tiers
  4. Business analytics — show owners their inquiry stats
  5. Conversion tracking — track what triggers upgrades

v2 improvements:
- Position + query attribution in inquiry logs
- Richer notification messages (count + context, not generic)
- Conversion event tracking (upgrade_clicked, upgrade_completed, boost_used)
- Outcome-focused upgrade copy (not feature lists)
- Ownership verification on upgrade lookup
- Expanded confirmation vocabulary (haan, ji, etc.)
- Session preserved on activation errors
- Aggregated notifications (daily count, not per-search)
- Query dedup in notifications (same user, same query = 1 signal)
- Inactive business nudge helper

WhatsApp commands:
  - "feature my business" / "upgrade my business" → Featured listing flow
  - "my stats" / "my analytics" → Show inquiry count for their business
  - "my plan" / "my subscription" → Show current subscription tier
  - "my leads" → Recent lead notification history
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

# Redis key prefix for upgrade sessions
_KEY_PREFIX = "upgrade:"

# ── Confirmation vocabulary (synced with registration + deals) ──
YES_WORDS = {
    "yes", "y", "confirm", "looks good", "correct", "ok",
    "👍", "sure", "yep", "yeah", "yea", "haan", "ji",
    "ha", "sahi hai", "theek hai", "done",
}
NO_WORDS = {"no", "n", "cancel", "nahi", "nah"}

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
        "label": "🚀 Premium",
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


def _key(wa_id: str) -> str:
    return f"{_KEY_PREFIX}{wa_id}"


def _get(wa_id: str, settings: Settings) -> dict | None:
    return get_session(_key(wa_id), settings)


def _save(wa_id: str, session: dict, settings: Settings) -> None:
    set_session(_key(wa_id), session, settings)


def _del(wa_id: str, settings: Settings) -> None:
    delete_session(_key(wa_id), settings)


def has_active_upgrade_session(wa_id: str, settings: Settings | None = None) -> bool:
    if settings is None:
        from config.settings import get_settings
        settings = get_settings()
    return session_exists(_key(wa_id), settings)


# ── Ownership check ────────────────────────────────────────────
def _is_owner(business: dict, wa_id: str) -> bool:
    """Check if wa_id owns this business (source_id prefix match)."""
    source_id = business.get("source_id", "")
    return source_id.startswith(f"user_{wa_id}_")


# ── Conversion tracking ──────────────────────────────────────────
def _track_event(event: str, wa_id: str, details: dict, settings: Settings) -> None:
    """
    Track conversion events for analytics. Best-effort, never blocks.
    Events: upgrade_clicked, upgrade_completed, downgrade, boost_used, trial_started
    """
    try:
        client = create_client(settings.SUPABASE_URL, settings.SUPABASE_KEY)
        client.table("notification_log").insert({
            "id": str(uuid.uuid4()),
            "business_id": details.get("business_id", ""),
            "business_name": details.get("business_name", ""),
            "owner_wa_id": wa_id,
            "search_query": event,
            "status": "conversion_event",
            "error_msg": str(details)[:500],
        }).execute()
    except Exception:
        pass  # Best-effort


# ── Intent detection ────────────────────────────────────────────
def detect_monetization_intent(message: str) -> str | None:
    """
    Detect monetization-related intents.
    Returns "upgrade", "stats", "plan", "leads", or None.
    """
    msg = message.lower().strip()

    upgrade_phrases = [
        "feature my business", "upgrade my business", "promote my business",
        "featured listing", "get featured", "upgrade listing",
        "boost my business", "premium listing", "upgrade my plan",
        "i want featured", "make my business featured",
        "upgrade",
    ]
    leads_phrases = [
        "my leads", "my notifications", "lead history",
        "who searched for me", "my lead notifications",
        "show my leads", "recent leads",
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
    for phrase in leads_phrases:
        if phrase in msg:
            return "leads"
    for phrase in stats_phrases:
        if phrase in msg:
            return "stats"
    for phrase in plan_phrases:
        if phrase in msg:
            return "plan"
    return None


# ── Start upgrade flow ──────────────────────────────────────────
def start_upgrade_flow(wa_id: str, settings: Settings | None = None) -> str:
    if settings is None:
        from config.settings import get_settings
        settings = get_settings()
    _save(wa_id, {
        "wa_id": wa_id,
        "step": UpgradeStep.BUSINESS_LOOKUP,
        "data": {},
        "matches": [],
    }, settings)

    _track_event("upgrade_clicked", wa_id, {"source": "direct_command"}, settings)

    return (
        "Let's get you more visibility! 🚀\n\n"
        "What's your *business name* or *phone number*?\n\n"
        "Type *cancel* anytime to exit."
    )


def _plan_menu(current_plan: str = "free") -> str:
    lines = ["Choose a plan (reply with the number):\n"]
    i = 1
    for key, p in PLANS.items():
        if key == current_plan:
            lines.append(f"{i}. {p['label']} — {p['price']} ✅ *Current*")
        else:
            if key == "featured":
                lines.append(
                    f"{i}. {p['label']} — {p['price']}\n"
                    f"   🔥 5x more visibility + daily digest placement\n"
                    f"   🏷️ 5 deals/month + analytics"
                )
            elif key == "premium":
                lines.append(
                    f"{i}. {p['label']} — {p['price']}\n"
                    f"   🔥 Top of every search + unlimited deals\n"
                    f"   📊 Full analytics + priority support"
                )
            else:
                lines.append(f"{i}. {p['label']} — {p['price']}\n   🏷️ 1 deal/month")
        i += 1
    return "\n".join(lines)


# ── Upgrade session handler ─────────────────────────────────────
def handle_upgrade_message(wa_id: str, message: str, settings: Settings) -> str:
    session = _get(wa_id, settings)
    if not session:
        return "No active session. Say *'upgrade'* to get started."

    msg = message.strip()
    if msg.lower() in ("cancel", "stop", "quit", "exit", "nevermind"):
        _del(wa_id, settings)
        return "No worries, cancelled 👍"

    return _handle_upgrade_step(session, msg, settings)


def _handle_upgrade_step(session: dict, msg: str, settings: Settings) -> str:
    step = session["step"]
    wa_id = session["wa_id"]

    if step == UpgradeStep.BUSINESS_LOOKUP:
        return _upgrade_lookup(session, msg, settings)

    elif step == UpgradeStep.SELECT_BUSINESS:
        return _upgrade_select(session, msg, settings)

    elif step == UpgradeStep.CHOOSE_PLAN:
        return _upgrade_choose_plan(session, msg, settings)

    elif step == UpgradeStep.CONFIRM:
        lower = msg.lower().strip()
        if lower in YES_WORDS:
            return _activate_plan(session, settings)
        elif lower in NO_WORDS:
            _del(wa_id, settings)
            return "No worries! Your current plan stays active. 🙏"
        return "Reply *yes* to confirm or *no* to cancel."

    return "Something went wrong. Type *cancel* to exit."


def _upgrade_lookup(session: dict, msg: str, settings: Settings) -> str:
    """Find business with ownership verification."""
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
            # Check if business exists but isn't owned by user
            if len(digits) >= 7:
                any_match = client.table("businesses").select("name").ilike("phone", f"%{digits[-10:]}%").limit(1).execute()
            else:
                any_match = client.table("businesses").select("name").ilike("name", f"%{msg}%").limit(1).execute()

            if any_match.data:
                return (
                    f"I found *{any_match.data[0]['name']}*, but it's not linked to your account.\n\n"
                    "You can only upgrade businesses you registered.\n"
                    "Need to register? Type *'add my business'*.\n\n"
                    "Type *cancel* to exit."
                )
            return (
                "I couldn't find that business. Check the name/phone, "
                "or *add your business first* by typing *'add my business'*.\n\n"
                "Type *cancel* to exit."
            )

        if len(result.data) == 1:
            b = result.data[0]
            current = _get_current_plan(b["id"], settings)
            data["business"] = b
            data["current_plan"] = current
            session["data"] = data
            session["step"] = UpgradeStep.CHOOSE_PLAN
            _save(wa_id, session, settings)
            featured_badge = " ⭐ Featured" if b.get("is_featured") else ""

            # Get recent inquiry count for social proof
            inquiry_count = _get_recent_inquiry_count(b["id"], settings)
            proof_line = ""
            if inquiry_count > 0:
                proof_line = f"\n👀 *{inquiry_count} people* searched for your category in the last 30 days\n"

            return (
                f"Found: *{b['name']}*{featured_badge}\n"
                f"📍 {b.get('city', '')}, {b.get('state', '')}\n"
                f"Current plan: *{PLANS[current]['label']}*\n"
                f"{proof_line}\n"
                f"{_plan_menu(current)}"
            )

        session["matches"] = result.data[:5]
        session["step"] = UpgradeStep.SELECT_BUSINESS
        _save(wa_id, session, settings)
        lines = ["You have multiple businesses. Which one? (reply with number)\n"]
        for i, b in enumerate(result.data[:5], 1):
            lines.append(f"{i}. *{b['name']}* — {b.get('city', '')}, {b.get('state', '')}")
        return "\n".join(lines)

    except Exception as e:
        logger.error(f"Upgrade lookup failed: {e}")
        # Don't delete session on transient error
        return "Sorry, something went wrong. Please try again. 🙏"


def _upgrade_select(session: dict, msg: str, settings: Settings) -> str:
    wa_id = session["wa_id"]
    matches = session.get("matches", [])
    data = session.get("data", {})
    try:
        idx = int(msg.strip()) - 1
        if 0 <= idx < len(matches):
            b = matches[idx]
            current = _get_current_plan(b["id"], settings)
            data["business"] = b
            data["current_plan"] = current
            session["data"] = data
            session["step"] = UpgradeStep.CHOOSE_PLAN
            _save(wa_id, session, settings)
            return (
                f"Selected: *{b['name']}*\n"
                f"Current plan: *{PLANS[current]['label']}*\n\n"
                f"{_plan_menu(current)}"
            )
    except (ValueError, IndexError):
        pass
    return "Please reply with a number from the list, or *cancel* to exit."


def _upgrade_choose_plan(session: dict, msg: str, settings: Settings) -> str:
    wa_id = session["wa_id"]
    data = session.get("data", {})
    plan_keys = list(PLANS.keys())
    try:
        idx = int(msg.strip()) - 1
        if 0 <= idx < len(plan_keys):
            chosen = plan_keys[idx]
            if chosen == data.get("current_plan"):
                return "That's already your current plan! Pick a different one, or *cancel*."
            if chosen == "free":
                data["chosen_plan"] = "free"
                session["data"] = data
                session["step"] = UpgradeStep.CONFIRM
                _save(wa_id, session, settings)
                return (
                    "Switch to *Free* plan?\n"
                    "You'll lose your Featured badge + limited to 1 deal/month.\n\n"
                    "Reply *yes* to confirm or *no* to cancel."
                )
            data["chosen_plan"] = chosen
            session["data"] = data
            session["step"] = UpgradeStep.CONFIRM
            _save(wa_id, session, settings)
            p = PLANS[chosen]

            # Outcome-focused copy (not feature list)
            if chosen == "featured":
                return (
                    f"*{p['label']}* — {p['price']}\n\n"
                    "Get *5x more visibility* for your business:\n\n"
                    "🔥 Show up *first* when customers search\n"
                    "📩 Appear in the *daily digest* to 100s of people\n"
                    "📊 See exactly *how many leads* you're getting\n"
                    "🏷️ Post up to *5 deals/month*\n\n"
                    "Reply *yes* to activate or *no* to cancel."
                )
            else:  # premium
                return (
                    f"*{p['label']}* — {p['price']}\n\n"
                    "Get *maximum visibility* — the full Mira advantage:\n\n"
                    "🔥 *Top of every search* + daily digest\n"
                    "🏷️ *Unlimited deals* — post as many as you want\n"
                    "📊 Full analytics + priority support\n"
                    "🚀 Best value for serious businesses\n\n"
                    "Reply *yes* to activate or *no* to cancel."
                )
    except (ValueError, IndexError):
        pass
    return f"Please pick a number.\n\n{_plan_menu(data.get('current_plan', 'free'))}"


def _activate_plan(session: dict, settings: Settings) -> str:
    """Activate the chosen plan in Supabase."""
    data = session["data"]
    wa_id = session["wa_id"]
    b = data["business"]
    chosen = data["chosen_plan"]
    p = PLANS[chosen]

    try:
        client = create_client(settings.SUPABASE_URL, settings.SUPABASE_KEY)

        client.table("businesses").update({
            "is_featured": p["featured"]
        }).eq("id", b["id"]).execute()

        now = datetime.now(timezone.utc)
        sub_row = {
            "id": str(uuid.uuid4()),
            "business_id": b["id"],
            "wa_id": wa_id,
            "plan": chosen,
            "status": "active",
            "deals_per_month": p["deals_per_month"],
            "starts_at": now.isoformat(),
            "expires_at": (now + timedelta(days=30)).isoformat() if chosen != "free" else None,
        }
        client.table("subscriptions").delete().eq("business_id", b["id"]).execute()
        client.table("subscriptions").insert(sub_row).execute()

        _del(wa_id, settings)
        logger.info(f"Plan activated: {b['name']} → {chosen} by {wa_id}")

        # Track conversion
        _track_event("upgrade_completed", wa_id, {
            "business_id": b["id"],
            "business_name": b["name"],
            "plan": chosen,
            "source": data.get("upgrade_source", "direct"),
        }, settings)

        if chosen == "free":
            _track_event("downgrade", wa_id, {
                "business_id": b["id"],
                "business_name": b["name"],
            }, settings)
            return (
                "Done! Your listing is back on the *Free* plan. ✅\n\n"
                "Want to upgrade again anytime? Just say *'upgrade'*."
            )

        stripe_links = {
            "featured": settings.STRIPE_FEATURED_LINK,
            "premium": settings.STRIPE_PREMIUM_LINK,
        }
        payment_link = stripe_links.get(chosen, "")

        msg = (
            f"🎉 *{b['name']}* is now *{p['label']}*!\n\n"
            "Here's what happens next:\n"
            "✅ You appear *first* in search — immediately\n"
            "✅ You're in the *daily digest* — starting tomorrow\n"
            "✅ Type *'my stats'* to see your analytics anytime\n\n"
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
        # Don't delete session on error — let user retry
        return (
            "Sorry, something went wrong activating your plan. 🙏\n\n"
            "Your data is saved — reply *yes* to try again, or *cancel* to exit."
        )


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


# ── Helper: recent inquiry count (for social proof) ─────────────
def _get_recent_inquiry_count(business_id: str, settings: Settings, days: int = 30) -> int:
    """Get inquiry count for social proof in upgrade flow."""
    try:
        client = create_client(settings.SUPABASE_URL, settings.SUPABASE_KEY)
        since = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
        result = (
            client.table("inquiry_logs")
            .select("id", count="exact")
            .eq("business_id", business_id)
            .gte("created_at", since)
            .execute()
        )
        return result.count if result.count else 0
    except Exception:
        return 0


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
    """
    Log inquiries for each business shown to a user.
    FIX #1: Now includes position (rank in results) and full query for attribution.
    """
    if not businesses:
        return
    try:
        client = create_client(settings.SUPABASE_URL, settings.SUPABASE_KEY)
        rows = []
        for position, b in enumerate(businesses, 1):
            rows.append({
                "id": str(uuid.uuid4()),
                "business_id": b.get("id"),
                "business_name": b.get("name", "Unknown"),
                "user_wa_id": user_wa_id,
                "inquiry_type": inquiry_type,
                "message_snippet": message_snippet[:100] if message_snippet else "",
                "city": b.get("city"),
                "state": b.get("state"),
                # Position attribution
                "error_msg": f"position={position}",
            })
        if rows:
            client.table("inquiry_logs").insert(rows).execute()
            logger.info(f"Logged {len(rows)} inquiries for user {user_wa_id}")

        # ── Aggregated lead notifications to business owners ──
        _notify_business_owners_aggregated(businesses, message_snippet, user_wa_id, settings)

    except Exception as e:
        logger.warning(f"Failed to log inquiries: {e}")


# In-memory caches for rate limiting + dedup
_notification_cache: dict[str, float] = {}
_daily_search_count: dict[str, int] = {}
_daily_search_date: str = ""
_query_dedup_cache: dict[str, float] = {}


def _notify_business_owners_aggregated(
    businesses: list[dict],
    search_query: str,
    user_wa_id: str,
    settings: Settings,
) -> None:
    """
    Send WhatsApp notification to business owners with aggregated context.
    FIX #2: Rich messages with search count + query context.
    FIX #8: Aggregated daily counts instead of per-search spam.
    FIX #10: Dedup same user + same query = 1 notification signal.
    """
    import asyncio
    from app.services.user_state_service import log_notification

    global _daily_search_date, _daily_search_count

    # Reset daily counts at midnight
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    if today != _daily_search_date:
        _daily_search_count.clear()
        _daily_search_date = today

    for b in businesses:
        try:
            source_id = (b.get("source_id") or "").strip()
            biz_id = b.get("id", "")
            biz_name = b.get("name", "your business")

            if not source_id:
                log_notification(biz_id, biz_name, "", search_query, "no_owner", settings=settings)
                continue

            # Extract owner wa_id from source_id
            owner_wa_id = ""
            if source_id.startswith("user_"):
                # source_id format: user_{wa_id}_{timestamp}
                parts = source_id.split("_")
                if len(parts) >= 2:
                    owner_wa_id = parts[1]
            if not owner_wa_id:
                owner_wa_id = source_id.replace("wa:", "").strip()
            if not owner_wa_id:
                log_notification(biz_id, biz_name, "", search_query, "no_owner", settings=settings)
                continue

            city = b.get("city", "")
            is_featured = b.get("is_featured", False)

            # FIX #10: Dedup — same user + same query within 1 hour = skip
            dedup_key = f"dedup:{biz_id}:{user_wa_id}:{search_query[:30]}"
            now = time.time()
            if now - _query_dedup_cache.get(dedup_key, 0) < 3600:
                continue  # Same user searched same thing recently
            _query_dedup_cache[dedup_key] = now

            # Track daily search count for this business
            count_key = f"count:{biz_id}"
            _daily_search_count[count_key] = _daily_search_count.get(count_key, 0) + 1
            today_count = _daily_search_count[count_key]

            # Rate limit: max 3 notifications per business per day
            cache_key = f"notif:{biz_id}"
            notif_count_key = f"notif_count:{biz_id}:{today}"
            daily_notifs = _notification_cache.get(notif_count_key, 0)
            if daily_notifs >= 3:
                log_notification(biz_id, biz_name, owner_wa_id, search_query, "rate_limited", settings=settings)
                continue

            # Also enforce 1-hour cooldown between notifications
            last_sent = _notification_cache.get(cache_key, 0)
            if now - last_sent < 3600:
                log_notification(biz_id, biz_name, owner_wa_id, search_query, "rate_limited", settings=settings)
                continue

            _notification_cache[cache_key] = now
            _notification_cache[notif_count_key] = daily_notifs + 1

            # FIX #2: Build rich notification with count + context
            query_preview = search_query[:60] if search_query else "a local service"

            if today_count == 1:
                msg = (
                    f"🔔 *New customer interest!*\n\n"
                    f"Someone searched for:\n"
                    f"👉 _{query_preview}_\n\n"
                    f"Your business *{biz_name}* appeared in results"
                )
            else:
                msg = (
                    f"🔥 *{today_count} searches today!*\n\n"
                    f"Latest search:\n"
                    f"👉 _{query_preview}_\n\n"
                    f"Your business *{biz_name}* is getting noticed"
                )

            if city:
                msg += f" in {city}"
            msg += " 👍\n"

            if not is_featured:
                msg += (
                    "\n📈 Featured businesses get *5x more visibility*\n"
                    "👉 Reply *\"upgrade\"* to appear first — $15/month"
                )
            else:
                msg += "\n✅ Your Featured badge helped you appear first!"

            # Send async notification
            from app.services.whatsapp_service import WhatsAppService
            whatsapp = WhatsAppService(settings)

            try:
                loop = asyncio.get_running_loop()
                loop.create_task(whatsapp.send_text_message(owner_wa_id, msg))
                log_notification(biz_id, biz_name, owner_wa_id, search_query, "sent", settings=settings)
            except RuntimeError:
                log_notification(biz_id, biz_name, owner_wa_id, search_query, "failed", "no event loop", settings=settings)

            logger.info(f"Lead notification sent: {biz_name} → {owner_wa_id}")

        except Exception as e:
            logger.warning(f"Lead notification failed for {b.get('name', '?')}: {e}")
            log_notification(b.get("id", ""), b.get("name", "?"), "", search_query, "failed", str(e), settings=settings)


# ── Business stats (for owners) ─────────────────────────────────
def get_business_stats(wa_id: str, settings: Settings) -> str:
    """Show inquiry stats for businesses owned by this wa_id."""
    try:
        client = create_client(settings.SUPABASE_URL, settings.SUPABASE_KEY)
        owner_prefix = f"user_{wa_id}_"

        # Find businesses owned by this user
        biz_result = (
            client.table("businesses")
            .select("id, name, city, state, is_featured")
            .ilike("source_id", f"{owner_prefix}%")
            .execute()
        )

        if not biz_result.data:
            return (
                "I couldn't find any businesses linked to your account.\n"
                "Add your business first by typing *'add my business'*."
            )

        lines = ["📊 *Your Business Analytics*\n"]
        total_inquiries = 0

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
            total_inquiries += count

            # Week-over-week comparison
            seven_days_ago = (datetime.now(timezone.utc) - timedelta(days=7)).isoformat()
            week_stats = (
                client.table("inquiry_logs")
                .select("id", count="exact")
                .eq("business_id", b["id"])
                .gte("created_at", seven_days_ago)
                .execute()
            )
            week_count = week_stats.count if week_stats.count else 0

            lines.append(
                f"🏪 *{b['name']}*{featured}\n"
                f"   📍 {b.get('city', '')}, {b.get('state', '')}\n"
                f"   👀 *{count}* inquiries (30 days)\n"
                f"   📈 *{week_count}* this week\n"
            )

        if any(not b.get("is_featured") for b in biz_result.data):
            lines.append(
                f"\n💡 Featured businesses get *5x more visibility*\n"
                "👉 Reply *\"upgrade\"* to appear first in search"
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
                "💡 Featured businesses get *5x more visibility*\n"
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
                    if days_left <= 5:
                        expire_str = f"⚠️ *{days_left} days left* — renew soon!"
                    else:
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


def get_notification_history(wa_id: str, settings: Settings, limit: int = 10) -> str:
    """Show recent lead notifications for businesses owned by this wa_id."""
    try:
        client = create_client(settings.SUPABASE_URL, settings.SUPABASE_KEY)
        owner_prefix = f"user_{wa_id}_"

        # Find businesses owned by this user (ownership-verified)
        biz_result = (
            client.table("businesses")
            .select("id, name")
            .ilike("source_id", f"{owner_prefix}%")
            .execute()
        )

        if not biz_result.data:
            return (
                "I couldn't find any businesses linked to your account.\n"
                "Add your business first by typing *'add my business'*."
            )

        biz_ids = [b["id"] for b in biz_result.data]

        # Fetch recent notifications
        all_notifs = []
        for biz_id in biz_ids:
            notifs = (
                client.table("notification_log")
                .select("business_name, search_query, status, created_at")
                .eq("business_id", biz_id)
                .order("created_at", desc=True)
                .limit(limit)
                .execute()
            )
            if notifs.data:
                all_notifs.extend(notifs.data)

        if not all_notifs:
            return (
                "No lead notifications yet for your business.\n\n"
                "When someone searches for your services, you'll see it here!\n"
                "💡 Tip: Reply *\"upgrade\"* to appear first in search results."
            )

        # Sort by created_at descending and take top N
        all_notifs.sort(key=lambda x: x.get("created_at", ""), reverse=True)
        all_notifs = all_notifs[:limit]

        lines = ["🔔 *Recent Lead Notifications*\n"]
        for n in all_notifs:
            query = n.get("search_query", "unknown search")[:50]
            status = n.get("status", "")
            biz = n.get("business_name", "your business")
            created = n.get("created_at", "")

            # Format date
            date_str = ""
            if created:
                try:
                    dt = datetime.fromisoformat(created.replace("Z", "+00:00"))
                    date_str = dt.strftime("%b %d, %I:%M %p")
                except Exception:
                    date_str = created[:10]

            status_icon = "✅" if status == "sent" else "⏳" if status == "rate_limited" else "❌"
            lines.append(
                f"{status_icon} *{biz}*\n"
                f"   🔍 _{query}_\n"
                f"   📅 {date_str}\n"
            )

        lines.append(
            "📊 Want full analytics? Reply *\"my stats\"*"
        )
        return "\n".join(lines)

    except Exception as e:
        logger.error(f"Failed to get notification history for {wa_id}: {e}")
        return "Sorry, couldn't load your lead history right now. Try again later. 🙏"


# ══════════════════════════════════════════════════════════════════
# INACTIVE BUSINESS NUDGE (called by daily cron)
# ══════════════════════════════════════════════════════════════════

async def nudge_inactive_businesses(settings: Settings) -> dict:
    """
    Send re-engagement message to business owners who haven't posted
    a deal in 7+ days. Called by daily cron.
    """
    from app.services.whatsapp_service import WhatsAppService

    try:
        client = create_client(settings.SUPABASE_URL, settings.SUPABASE_KEY)
        seven_days_ago = (datetime.now(timezone.utc) - timedelta(days=7)).isoformat()

        # Get all businesses with owners
        businesses = (
            client.table("businesses")
            .select("id, name, source_id")
            .neq("source_id", "")
            .limit(200)
            .execute()
        )

        if not businesses.data:
            return {"nudged": 0}

        whatsapp = WhatsAppService(settings)
        nudged = 0
        seen_owners: set[str] = set()

        for b in businesses.data:
            source_id = b.get("source_id", "")
            if not source_id.startswith("user_"):
                continue
            parts = source_id.split("_")
            if len(parts) < 2:
                continue
            owner_wa_id = parts[1]
            if owner_wa_id in seen_owners:
                continue

            # Check if they posted a deal recently
            recent_deals = (
                client.table("deals")
                .select("id")
                .eq("business_id", b["id"])
                .gte("created_at", seven_days_ago)
                .limit(1)
                .execute()
            )
            if recent_deals.data:
                continue  # Active — skip

            # Check inquiry count for social proof
            inquiry_count = _get_recent_inquiry_count(b["id"], settings, days=7)
            seen_owners.add(owner_wa_id)

            if inquiry_count > 0:
                msg = (
                    f"👋 Hi! *{b['name']}* had *{inquiry_count} searches* this week.\n\n"
                    "Businesses that post deals get *3x more leads*.\n"
                    "👉 Reply *\"post a deal\"* to attract more customers!"
                )
            else:
                msg = (
                    f"👋 Hi! It's been a while since *{b['name']}* was active on Mira.\n\n"
                    "💡 Tip: Businesses that post deals get *3x more leads*.\n"
                    "👉 Reply *\"post a deal\"* to get started!"
                )

            try:
                await whatsapp.send_text_message(owner_wa_id, msg)
                nudged += 1
            except Exception as e:
                logger.warning(f"Failed to nudge {owner_wa_id}: {e}")

        summary = {"nudged": nudged}
        logger.info(f"Inactive business nudge: {summary}")
        return summary

    except Exception as e:
        logger.error(f"Inactive nudge cron failed: {e}")
        return {"error": str(e)}
