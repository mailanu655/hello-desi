"""
Mira — Daily Metro Digest Service

An opt-in daily WhatsApp newsletter that delivers curated content
to users in their metro area. This is NOT optional — it's a core
retention engine and future ad revenue channel.

Format:
  🌅 *Good Morning, Dallas Desis!* — Sat, Mar 28

  🏪 *New This Week*
  • Hyderabad House opened in Plano — biryanis & more
  • 5 new businesses added in DFW this week

  🔥 *Top Deal*
  Bombay Bazaar: 20% off groceries this weekend (expires Sun)

  📅 *Upcoming*
  Holi Festival at Plano Event Center — Apr 5

  ⭐ *Sponsored*
  Taj Palace — Now open for weekend brunch! Book: 469-555-1234

  ────────────
  Reply STOP to unsubscribe

MVP scope:
  - Opt-in/opt-out via WhatsApp commands
  - Subscriber table in Supabase (digest_subscribers)
  - Pull new businesses, active deals, featured businesses
  - One sponsored slot for featured/premium businesses
  - Runs daily at 8am EST
"""

import logging
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
            "• New businesses & openings\n"
            "• Top deals of the day\n"
            "• Upcoming community events\n"
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


def _get_active_deals(city: str, settings: Settings) -> list[dict]:
    """
    Get active deals for this city, ranked for digest.
    Prefers: featured businesses first, then urgency (expiring soon), then recency.
    Returns top 3 deals.
    """
    try:
        client = create_client(settings.SUPABASE_URL, settings.SUPABASE_KEY)
        now = datetime.now(timezone.utc)
        now_iso = now.isoformat()

        # Fetch more than needed for ranking
        result = (
            client.table("deals")
            .select("title, description, business_name, business_id, expires_at, created_at")
            .ilike("city", f"%{city}%")
            .eq("is_active", True)
            .gte("expires_at", now_iso)
            .order("created_at", desc=True)
            .limit(15)
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

        # Score and rank
        def _score(d: dict) -> float:
            s = 0.0
            if d.get("business_id") in featured_ids:
                s += 100
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
        return deals[:3]

    except Exception as e:
        logger.warning(f"Failed to get deals for {city}: {e}")
        return []


def _get_featured_businesses(city: str, settings: Settings) -> list[dict]:
    """Get featured businesses for sponsored slot."""
    try:
        client = create_client(settings.SUPABASE_URL, settings.SUPABASE_KEY)
        result = (
            client.table("businesses")
            .select("name, category, phone, city, state")
            .ilike("city", f"%{city}%")
            .eq("is_featured", True)
            .limit(2)
            .execute()
        )
        return result.data or []
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


def build_digest_message(city: str, settings: Settings) -> str:
    """Build the daily digest message for a city — Mira brand voice."""
    now = datetime.now(timezone.utc)

    new_biz = _get_new_businesses(city, settings)
    deals = _get_active_deals(city, settings)
    featured = _get_featured_businesses(city, settings)
    total_count = _get_total_business_count(city, settings)

    # ── Header — clean Mira style ──
    msg = f"🌆 *{city} with Mira*\n\n"

    # ── Grocery / New Businesses ──
    if new_biz:
        msg += "🛒 *New This Week*\n"
        for b in new_biz[:3]:
            cat = b.get("category", "")
            cat_str = f" — {cat}" if cat else ""
            msg += f"• {b['name']}{cat_str}\n"
        if len(new_biz) > 3:
            extra = len(new_biz) - 3
            msg += f"• +{extra} more listings\n"
        msg += "\n"
    elif total_count > 0:
        msg += f"🛒 *{total_count} businesses* in {city}\n"
        msg += "Know a local desi business? Share Mira with them 🙌\n\n"

    # ── Deals ──
    if deals:
        msg += "💸 *Deals*\n"
        for d in deals[:3]:
            biz_name = d.get("business_name", "Local Business")
            title = d.get("title", "")
            urgency = ""
            if d.get("expires_at"):
                try:
                    exp = datetime.fromisoformat(d["expires_at"].replace("Z", "+00:00"))
                    days_left = (exp - now).days
                    if days_left <= 0:
                        urgency = " ⏰ Last day!"
                    elif days_left <= 2:
                        urgency = " ⏰ Ends soon!"
                except Exception:
                    pass
            # Freshness
            freshness = ""
            if d.get("created_at"):
                try:
                    cr = datetime.fromisoformat(d["created_at"].replace("Z", "+00:00"))
                    if (now - cr).days <= 1:
                        freshness = "🆕 "
                except Exception:
                    pass
            msg += f"• {freshness}{biz_name}: {title}{urgency}\n"
        msg += "\n"

    # ── Sponsored Slot (Featured businesses) ──
    if featured:
        f_biz = featured[0]
        phone = f_biz.get("phone", "")
        cat = f_biz.get("category", "")
        phone_str = f" — {phone}" if phone else ""
        cat_str = f" ({cat})" if cat else ""
        msg += f"⭐ {f_biz['name']}{cat_str}{phone_str}\n\n"

    # ── Footer — conversational Mira ──
    msg += "👉 Reply *1* for deals | *2* for services\n"
    msg += "Reply *stop digest* to unsubscribe"

    return msg


# ── Main Send Function ────────────────────────────────────────────

async def send_daily_digest(settings: Settings) -> dict:
    """
    Send daily digest to all active subscribers.
    Called by cron at 8am EST.
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
        logger.info("No active digest subscribers found")
        return {"sent": 0, "total_subscribers": 0}

    whatsapp = WhatsAppService(settings)
    sent = 0
    failed = 0

    # Group by city to avoid rebuilding the same digest
    city_digests: dict[str, str] = {}

    for sub in subscribers.data:
        city = sub.get("city", "").strip().title()
        wa_id = sub.get("wa_id", "")

        if not city or not wa_id:
            continue

        # Cache digest per city
        if city not in city_digests:
            city_digests[city] = build_digest_message(city, settings)

        try:
            await whatsapp.send_text_message(wa_id, city_digests[city])
            sent += 1
        except Exception as e:
            logger.error(f"Failed to send digest to {wa_id}: {e}")
            failed += 1

    summary = {
        "total_subscribers": len(subscribers.data),
        "cities": len(city_digests),
        "sent": sent,
        "failed": failed,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }

    logger.info(f"Daily digest complete: {summary}")
    return summary
