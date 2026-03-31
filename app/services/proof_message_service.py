"""
Mira — Weekly Proof Message Service (v2 — conversion-optimized)

Sends business owners a WhatsApp message showing how many people
asked about their business in the past week. This is the single
highest-leverage conversion tool — it creates the "aha moment"
that turns free users into paying customers.

v2 improvements:
- Urgency framing ("you're missing customers")
- Category benchmark comparison ("top businesses got 25–40 searches")
- Trend display with percentage (📈 +40% vs last week)
- Stats → action tie ("0 deals posted → you're missing conversions")
- Boost CTA on every proof message (highest-intent moment)
- Conversion tracking (sent → clicked → converted)
- Intelligent skip for businesses with 4+ weeks of zero searches
- Deal count tie-in (active deals vs none)

Message variants:
  🟢 ACTIVE  — high searches, show momentum, push upgrade/boost
  🟡 QUIET   — zero this week, benchmark against category
  🔵 NEW     — first 2 weeks, onboarding + tips
  ⚪ DORMANT — 4+ weeks zero, reduced frequency (bi-weekly)

Schedule:
  Runs every Monday at 10am EST via /api/v1/tasks/proof-messages
  Triggered by Render Cron Job or external scheduler.
"""

import json
import logging
from datetime import datetime, timedelta, timezone

from supabase import create_client
from config.settings import Settings

logger = logging.getLogger(__name__)


# ── Data Fetchers ───────────────────────────────────────────────

def get_all_businesses_with_owners(settings: Settings) -> list[dict]:
    """
    Fetch all businesses that have a source_id (owner's wa_id).
    Returns list of dicts with business info + owner wa_id.
    """
    try:
        client = create_client(settings.SUPABASE_URL, settings.SUPABASE_KEY)
        result = (
            client.table("businesses")
            .select("id, name, city, state, is_featured, source_id, category, created_at")
            .neq("source_id", "")
            .execute()
        )
        return result.data or []
    except Exception as e:
        logger.error(f"Failed to fetch businesses with owners: {e}")
        return []


def get_inquiry_count(
    business_id: str,
    settings: Settings,
    days: int = 7,
) -> int:
    """Count inquiries for a business in the last N days."""
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
    except Exception as e:
        logger.error(f"Failed to count inquiries for {business_id}: {e}")
        return 0


def _get_category_benchmark(category: str, city: str, settings: Settings) -> int:
    """
    Get the top-performer search count for this category in this city.
    Returns the max inquiry count among businesses in the same category.
    Used as a benchmark: "Top restaurants got 35 searches this week".
    """
    try:
        client = create_client(settings.SUPABASE_URL, settings.SUPABASE_KEY)
        since = (datetime.now(timezone.utc) - timedelta(days=7)).isoformat()

        # Get all businesses in this category + city
        biz_result = (
            client.table("businesses")
            .select("id")
            .ilike("city", f"%{city}%")
            .ilike("category", f"%{category}%")
            .execute()
        )

        if not biz_result.data:
            return 0

        biz_ids = [b["id"] for b in biz_result.data]

        # Count inquiries for each, find max
        max_count = 0
        for bid in biz_ids[:10]:  # Cap at 10 to avoid excessive queries
            count = get_inquiry_count(bid, settings, days=7)
            max_count = max(max_count, count)

        return max_count

    except Exception as e:
        logger.warning(f"Failed to get category benchmark: {e}")
        return 0


def _get_active_deal_count(business_id: str, settings: Settings) -> int:
    """Count active deals for this business."""
    try:
        client = create_client(settings.SUPABASE_URL, settings.SUPABASE_KEY)
        now = datetime.now(timezone.utc).isoformat()
        result = (
            client.table("deals")
            .select("id", count="exact")
            .eq("business_id", business_id)
            .eq("is_active", True)
            .gte("expires_at", now)
            .execute()
        )
        return result.count if result.count else 0
    except Exception:
        return 0


def _get_consecutive_zero_weeks(business_id: str, settings: Settings) -> int:
    """
    Check how many consecutive weeks a business has had zero inquiries.
    Used to identify dormant businesses for reduced send frequency.
    """
    try:
        client = create_client(settings.SUPABASE_URL, settings.SUPABASE_KEY)
        now = datetime.now(timezone.utc)

        for weeks in range(1, 6):  # Check up to 5 weeks back
            since = (now - timedelta(days=7 * weeks)).isoformat()
            until = (now - timedelta(days=7 * (weeks - 1))).isoformat()
            result = (
                client.table("inquiry_logs")
                .select("id", count="exact")
                .eq("business_id", business_id)
                .gte("created_at", since)
                .lt("created_at", until)
                .execute()
            )
            if result.count and result.count > 0:
                return weeks - 1
        return 5  # 5+ weeks of zero

    except Exception:
        return 0


# ── Tracking ────────────────────────────────────────────────────

def _track_proof_event(
    event: str, wa_id: str, details: dict, settings: Settings,
) -> None:
    """Log proof message events for conversion funnel tracking."""
    try:
        client = create_client(settings.SUPABASE_URL, settings.SUPABASE_KEY)
        client.table("notification_log").insert({
            "business_id": details.get("business_id", ""),
            "business_name": details.get("business_name", ""),
            "owner_wa_id": wa_id,
            "search_query": event,
            "status": "proof_event",
            "details": json.dumps(details),
        }).execute()
    except Exception as e:
        logger.warning(f"Proof event tracking failed: {e}")


def mark_proof_sent(wa_id: str, settings: Settings) -> None:
    """Set a Redis marker so we can attribute post-proof actions."""
    try:
        from app.services.session_store import _get_redis
        r = _get_redis(settings)
        if r:
            # Marker lasts 4 hours — if user acts within 4h, it's attributed
            r.setex(f"proof_sent:{wa_id}", 14400, "1")
    except Exception:
        pass


def track_proof_action(wa_id: str, action: str, settings: Settings) -> None:
    """
    Check if user recently received a proof message and log the attribution.
    Called from webhook when user does 'post deal', 'boost', or 'upgrade'.
    """
    try:
        from app.services.session_store import _get_redis
        r = _get_redis(settings)
        if not r:
            return
        if r.get(f"proof_sent:{wa_id}"):
            _track_proof_event("weekly_report_action", wa_id, {
                "action": action,
            }, settings)
            logger.info(f"Proof-attributed action: {wa_id} → {action}")
    except Exception:
        pass


# ── Category-Specific CTAs ──────────────────────────────────────

CATEGORY_CTAS: dict[str, str] = {
    "restaurant": "Add a lunch or dinner deal today 🍛",
    "grocery": "Run a weekend discount on groceries 🛒",
    "salon": "Offer a first-visit discount 💇",
    "beauty": "Promote a beauty package deal 💅",
    "temple": "Share upcoming events with the community 🙏",
    "legal": "Offer a free initial consultation 📋",
    "tax": "Promote seasonal tax prep specials 📊",
    "real estate": "List an open house or rental deal 🏠",
    "tutoring": "Offer a free trial session 📚",
    "childcare": "Share availability for new families 👶",
    "doctor": "Promote new patient specials 🏥",
    "insurance": "Offer a free policy review 🛡️",
    "auto": "Run a service discount special 🚗",
    "it": "Advertise a free consultation 💻",
}


def _get_category_cta(category: str) -> str:
    """Get a category-specific call-to-action, or a generic one."""
    if not category:
        return "Post a deal to attract new customers"
    cat_lower = category.lower()
    for key, cta in CATEGORY_CTAS.items():
        if key in cat_lower:
            return cta
    return "Post a deal to attract new customers"


def _shortcut_footer() -> str:
    """1-tap shortcut menu appended to every proof message."""
    return (
        "\n\n─────────────\n"
        "⚡ *Quick actions:*\n"
        "Reply *post deal* · *boost* · *my stats*"
    )


# ── Message Builder ─────────────────────────────────────────────

def build_proof_message(
    business: dict,
    this_week: int,
    last_week: int,
    benchmark: int = 0,
    active_deals: int = 0,
) -> str:
    """
    Build the weekly proof WhatsApp message for a business owner.

    v2.1 — conversion-optimized with urgency framing, benchmarks,
    trend percentages, category-specific CTAs, missed-opportunity
    framing, and 1-tap shortcut footer.

    Variants:
    🟢 ACTIVE  — has inquiries this week
    🟡 QUIET   — zero this week, had some before
    🔵 NEW     — brand new listing (< 14 days)
    ⚪ DORMANT — established, zero activity
    """
    name = business.get("name", "your business")
    is_featured = business.get("is_featured", False)
    city = business.get("city", "")
    category = business.get("category", "")
    cat_cta = _get_category_cta(category)

    # Check if new (< 14 days old)
    is_new = False
    if business.get("created_at"):
        try:
            created = datetime.fromisoformat(
                business["created_at"].replace("Z", "+00:00")
            )
            is_new = (datetime.now(timezone.utc) - created).days < 14
        except Exception:
            pass

    # ── 🟢 ACTIVE: has inquiries this week ──────────────────────
    if this_week > 0:
        # Trend line with percentage
        if last_week > 0:
            diff = this_week - last_week
            pct = round((diff / last_week) * 100)
            if diff > 0:
                trend = f"📈 *+{pct}%* vs last week ({last_week} → {this_week})"
            elif diff < 0:
                trend = f"📉 *{pct}%* vs last week ({last_week} → {this_week})"
            else:
                trend = f"➡️ Holding steady at {this_week}"
        else:
            trend = "🎉 Your first week with searches!"

        msg = (
            f"📊 *Weekly Report — {name}*\n\n"
            f"🔥 *{this_week} people* searched for your business last week!\n"
            f"{trend}\n"
        )

        # Missed opportunity framing — make loss tangible
        msg += (
            f"\nThat's *{this_week} potential customers* "
            "who were looking for exactly what you offer.\n"
        )

        # Benchmark comparison
        if benchmark > this_week:
            msg += (
                f"\n📊 Top {category or 'businesses'} in {city or 'your area'} "
                f"got *{benchmark}* searches\n"
            )

        # Deal tie-in — critical conversion moment
        if active_deals == 0:
            msg += (
                f"\n⚠️ *You have 0 active deals — you're missing conversions*\n"
                f"👉 {cat_cta}\n"
                "👉 Reply *\"post deal\"* to get started 🚀\n"
            )
        else:
            msg += (
                f"\n✅ You have *{active_deals} active deal{'s' if active_deals > 1 else ''}*"
                " — keep it up!\n"
            )

        # Upgrade / boost CTA
        if not is_featured:
            msg += (
                "\n💎 *Want to appear first in search results?*\n"
                "Featured businesses get *3x more visibility*\n"
                "👉 Reply *\"upgrade\"* — plans from $15/mo\n"
            )

        # Boost CTA (always show — highest intent moment)
        msg += (
            "\n🚀 *Want instant visibility?*\n"
            "Boost your deal for *$4.99* — top of results for 24h\n"
            "👉 Reply *\"boost\"*"
        )

        msg += _shortcut_footer()
        return msg

    # ── 🟡 QUIET: zero this week, had activity before ───────────
    if last_week > 0:
        msg = (
            f"📊 *Weekly Report — {name}*\n\n"
            f"⚠️ No searches last week\n"
            f"_(Had {last_week} the week before)_\n"
        )

        # Benchmark — create competition
        if benchmark > 0:
            msg += (
                f"\n📊 Similar {category or 'businesses'} in {city or 'your area'} "
                f"are getting *{benchmark}* searches\n"
                "\nCustomers are looking — they're just not finding you.\n"
            )
        else:
            msg += "\nSearches come in waves — let's get you back on top.\n"

        # Category-specific action tie-in
        if active_deals == 0:
            msg += (
                f"\n💡 *Tip:* {cat_cta}\n"
                "👉 Reply *\"post deal\"* — deals attract 2x more views\n"
            )

        if not is_featured:
            msg += (
                "👉 Reply *\"upgrade\"* — Featured listings stay visible in slow weeks\n"
            )

        msg += (
            "\n🚀 Or reply *\"boost\"* for instant top placement ($4.99)\n"
        )

        msg += _shortcut_footer()
        return msg

    # ── 🔵 NEW LISTING (< 14 days, zero activity) ──────────────
    if is_new:
        msg = (
            f"👋 *Welcome to Mira!*\n\n"
            f"Your business *{name}* is live"
        )
        if city:
            msg += f" in *{city}*"
        msg += ".\n\n"

        # Benchmark to create aspiration
        if benchmark > 0:
            msg += (
                f"📊 Top {category or 'businesses'} in your area "
                f"got *{benchmark} searches* last week\n\n"
            )

        msg += (
            "⏳ Customers are already searching in your area.\n\n"
            f"💡 *Quick win:* {cat_cta}\n"
            "👉 Reply *\"post deal\"* — post your first deal\n"
            "👉 Reply *\"upgrade\"* — appear first in results\n"
            "\n_Your next weekly report comes Monday!_"
        )

        msg += _shortcut_footer()
        return msg

    # ── ⚪ DORMANT (established, zero activity) ─────────────────
    msg = (
        f"📊 *Weekly Report — {name}*\n\n"
        f"No searches this week"
    )
    if city:
        msg += f" in {city}"
    msg += ".\n"

    if benchmark > 0:
        msg += (
            f"\n📊 But similar businesses are getting *{benchmark}* searches!\n"
            "\nCustomers are searching — they're just not finding you.\n"
        )
    else:
        msg += "\n"

    if active_deals == 0:
        msg += f"💡 *Tip:* {cat_cta}\n"
        msg += "👉 Reply *\"post deal\"* to attract customers\n"

    if not is_featured:
        msg += "👉 Reply *\"upgrade\"* to appear higher in search\n"

    msg += "👉 Reply *\"boost\"* for instant top placement ($4.99)"
    msg += _shortcut_footer()

    return msg


# ── Mid-Week Mini Proof ─────────────────────────────────────────

def build_midweek_nudge(
    business: dict,
    searches_so_far: int,
    active_deals: int = 0,
) -> str | None:
    """
    Build a light mid-week nudge (Wednesday).
    Only sent if business has searches so far this week.
    Returns None if not worth sending.
    """
    if searches_so_far <= 0:
        return None

    name = business.get("name", "your business")
    category = business.get("category", "")
    cat_cta = _get_category_cta(category)

    msg = (
        f"🔥 *{searches_so_far} people* searched for *{name}* so far this week!\n\n"
        "Don't miss them — customers are searching *right now*.\n"
    )

    if active_deals == 0:
        msg += (
            f"\n💡 {cat_cta}\n"
            "👉 Reply *\"post deal\"* to capture this demand 🚀\n"
        )
    else:
        msg += "\n✅ Your deals are live — keep the momentum!\n"

    msg += (
        "\n🚀 Reply *\"boost\"* for instant top placement ($4.99)"
    )
    msg += _shortcut_footer()

    return msg


async def send_midweek_nudges(settings: Settings) -> dict:
    """
    Send mid-week mini proof messages to business owners with searches.
    Called by cron on Wednesday at 11am EST.
    Only sends to businesses that have searches so far this week.
    """
    from app.services.whatsapp_service import WhatsAppService

    businesses = get_all_businesses_with_owners(settings)
    whatsapp = WhatsAppService(settings)

    sent = 0
    skipped = 0
    failed = 0

    # Group by owner
    owner_businesses: dict[str, list[dict]] = {}
    for biz in businesses:
        owner_id = biz.get("source_id", "").strip()
        if not owner_id:
            skipped += 1
            continue
        wa_id = owner_id
        if wa_id.startswith("user_"):
            parts = wa_id.split("_")
            if len(parts) >= 2:
                wa_id = parts[1]
        wa_id = wa_id.replace("wa:", "").strip()
        if not wa_id:
            skipped += 1
            continue
        owner_businesses.setdefault(wa_id, []).append(biz)

    # Only send for current partial week (Mon-Wed = ~3 days)
    days_into_week = min(datetime.now(timezone.utc).weekday() + 1, 4)

    for wa_id, biz_list in owner_businesses.items():
        for biz in biz_list:
            try:
                searches_so_far = get_inquiry_count(
                    biz["id"], settings, days=days_into_week
                )

                if searches_so_far <= 0:
                    skipped += 1
                    continue

                active_deals = _get_active_deal_count(biz["id"], settings)
                message = build_midweek_nudge(biz, searches_so_far, active_deals)

                if not message:
                    skipped += 1
                    continue

                await whatsapp.send_text_message(wa_id, message)
                sent += 1
                mark_proof_sent(wa_id, settings)

                _track_proof_event("midweek_nudge_sent", wa_id, {
                    "business_id": biz["id"],
                    "business_name": biz.get("name", ""),
                    "searches_so_far": searches_so_far,
                    "active_deals": active_deals,
                }, settings)

            except Exception as e:
                logger.error(f"Failed to send midweek nudge for {biz.get('name')}: {e}")
                failed += 1

    summary = {
        "total_businesses": len(businesses),
        "sent": sent,
        "skipped": skipped,
        "failed": failed,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    logger.info(f"Midweek nudge run complete: {summary}")
    return summary


# ── Main Send Functions ─────────────────────────────────────────

async def send_proof_messages(settings: Settings) -> dict:
    """
    Main entry point: send weekly proof messages to all business owners.

    v2 improvements:
    - Category benchmarks included in each message
    - Active deal count tie-in
    - Dormant business skip (4+ weeks zero → bi-weekly only)
    - Conversion tracking events

    Returns a summary dict with counts of sent/skipped/failed.
    """
    from app.services.whatsapp_service import WhatsAppService

    businesses = get_all_businesses_with_owners(settings)
    whatsapp = WhatsAppService(settings)

    sent = 0
    skipped = 0
    failed = 0
    dormant_skipped = 0

    # Group businesses by owner (one owner might have multiple businesses)
    owner_businesses: dict[str, list[dict]] = {}
    for biz in businesses:
        owner_id = biz.get("source_id", "").strip()
        if not owner_id:
            skipped += 1
            continue
        # Extract wa_id from source_id (format: "user_{wa_id}_..." or "wa:{wa_id}")
        wa_id = owner_id
        if wa_id.startswith("user_"):
            parts = wa_id.split("_")
            if len(parts) >= 2:
                wa_id = parts[1]
        wa_id = wa_id.replace("wa:", "").strip()
        if not wa_id:
            skipped += 1
            continue
        owner_businesses.setdefault(wa_id, []).append(biz)

    logger.info(
        f"Proof messages: {len(businesses)} businesses, "
        f"{len(owner_businesses)} unique owners"
    )

    # Check if this is an odd or even week (for bi-weekly dormant sends)
    week_number = datetime.now(timezone.utc).isocalendar()[1]
    is_even_week = week_number % 2 == 0

    for wa_id, biz_list in owner_businesses.items():
        for biz in biz_list:
            try:
                this_week = get_inquiry_count(biz["id"], settings, days=7)
                last_week = get_inquiry_count(biz["id"], settings, days=14) - this_week
                last_week = max(last_week, 0)

                # ── Dormant skip: 4+ weeks zero → bi-weekly only ──
                if this_week == 0 and last_week == 0:
                    zero_weeks = _get_consecutive_zero_weeks(biz["id"], settings)
                    if zero_weeks >= 4 and not is_even_week:
                        dormant_skipped += 1
                        logger.info(
                            f"Skipping dormant business {biz['name']} "
                            f"({zero_weeks} zero weeks, odd week)"
                        )
                        continue

                # Get benchmark and deal count for richer messaging
                benchmark = _get_category_benchmark(
                    biz.get("category", ""),
                    biz.get("city", ""),
                    settings,
                )
                active_deals = _get_active_deal_count(biz["id"], settings)

                message = build_proof_message(
                    biz, this_week, last_week,
                    benchmark=benchmark,
                    active_deals=active_deals,
                )
                await whatsapp.send_text_message(wa_id, message)
                sent += 1
                mark_proof_sent(wa_id, settings)

                # Track conversion funnel
                _track_proof_event("weekly_report_sent", wa_id, {
                    "business_id": biz["id"],
                    "business_name": biz.get("name", ""),
                    "this_week": this_week,
                    "last_week": last_week,
                    "benchmark": benchmark,
                    "active_deals": active_deals,
                    "is_featured": biz.get("is_featured", False),
                }, settings)

                logger.info(
                    f"Proof message sent: {biz['name']} → {wa_id} "
                    f"(this={this_week}, last={last_week}, "
                    f"bench={benchmark}, deals={active_deals})"
                )

            except Exception as e:
                logger.error(f"Failed to send proof message for {biz.get('name')}: {e}")
                failed += 1

    summary = {
        "total_businesses": len(businesses),
        "unique_owners": len(owner_businesses),
        "sent": sent,
        "skipped": skipped,
        "dormant_skipped": dormant_skipped,
        "failed": failed,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }

    logger.info(f"Proof message run complete: {summary}")
    return summary


async def send_proof_message_single(
    wa_id: str,
    settings: Settings,
) -> str:
    """
    Send proof messages for all businesses owned by a single wa_id.
    Used for manual testing via WhatsApp command "my weekly report".
    """
    from app.services.whatsapp_service import WhatsAppService

    try:
        client = create_client(settings.SUPABASE_URL, settings.SUPABASE_KEY)
        biz_result = (
            client.table("businesses")
            .select("id, name, city, state, is_featured, source_id, category, created_at")
            .ilike("source_id", f"%{wa_id}%")
            .execute()
        )

        if not biz_result.data:
            return (
                "I couldn't find any businesses linked to your account.\n"
                "Add your business first by typing *'add my business'*."
            )

        whatsapp = WhatsAppService(settings)

        for biz in biz_result.data:
            this_week = get_inquiry_count(biz["id"], settings, days=7)
            last_week = get_inquiry_count(biz["id"], settings, days=14) - this_week
            last_week = max(last_week, 0)

            benchmark = _get_category_benchmark(
                biz.get("category", ""),
                biz.get("city", ""),
                settings,
            )
            active_deals = _get_active_deal_count(biz["id"], settings)

            message = build_proof_message(
                biz, this_week, last_week,
                benchmark=benchmark,
                active_deals=active_deals,
            )
            await whatsapp.send_text_message(wa_id, message)

            # Track manual request
            _track_proof_event("weekly_report_manual", wa_id, {
                "business_id": biz["id"],
                "business_name": biz.get("name", ""),
                "this_week": this_week,
                "last_week": last_week,
            }, settings)

        return ""  # Messages already sent directly

    except Exception as e:
        logger.error(f"Failed to send single proof message for {wa_id}: {e}")
        return "Sorry, couldn't generate your weekly report right now. Try again later. 🙏"
