"""
Mira — Weekly Proof Message Service

Sends business owners a WhatsApp message showing how many people
asked about their business in the past week. This is the single
highest-leverage conversion tool — it creates the "aha moment"
that turns free users into paying customers.

Proof message format:
  📊 Weekly Report for *Taj Palace*
  🔥 12 people asked about your business this week!
  That's 3 more than last week.

  ⭐ Featured businesses get 3x more visibility.
  Type "feature my business" to upgrade →

Schedule:
  Runs every Monday at 10am EST via /api/v1/proof-messages/send
  Triggered by Render Cron Job or external scheduler.
"""

import logging
from datetime import datetime, timedelta, timezone

from supabase import create_client
from config.settings import Settings

logger = logging.getLogger(__name__)


def get_all_businesses_with_owners(settings: Settings) -> list[dict]:
    """
    Fetch all businesses that have a source_id (owner's wa_id).
    Returns list of dicts with business info + owner wa_id.
    """
    try:
        client = create_client(settings.SUPABASE_URL, settings.SUPABASE_KEY)
        result = (
            client.table("businesses")
            .select("id, name, city, state, is_featured, source_id, category")
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


def build_proof_message(
    business: dict,
    this_week: int,
    last_week: int,
) -> str:
    """
    Build the weekly proof WhatsApp message for a business owner.

    Three variants based on activity level:
    1. Active (>0 inquiries) — show count + trend + upgrade nudge
    2. Zero this week but had some before — encourage them
    3. Brand new / always zero — welcome + tips
    """
    name = business.get("name", "your business")
    is_featured = business.get("is_featured", False)
    city = business.get("city", "")

    # ── Active business: has inquiries this week ──
    if this_week > 0:
        # Trend line
        if last_week > 0:
            diff = this_week - last_week
            if diff > 0:
                trend = f"📈 {diff} more than last week"
            elif diff < 0:
                trend = f"📉 Down {abs(diff)} from last week"
            else:
                trend = "Holding steady"
        else:
            trend = "Your first week with inquiries!"

        msg = (
            f"Hi! This is Mira 👋\n\n"
            f"This week for *{name}*:\n"
            f"• *{this_week} people* searched for your category\n"
            f"• Your business appeared in results\n"
            f"• {trend}\n"
        )

        if not is_featured:
            msg += (
                "\n👉 Upgrade to be featured and get more leads\n"
                "Plans start at $15/month\n"
                "Reply *\"upgrade\"* to activate"
            )
        else:
            msg += (
                "\n✅ Your Featured badge is working!\n"
                "Type *\"my stats\"* for details"
            )

        return msg

    # ── No inquiries this week, but had some before ──
    if last_week > 0:
        msg = (
            f"Hi! This is Mira 👋\n\n"
            f"Quiet week for *{name}* — no new inquiries\n"
            f"(Had {last_week} last week)\n\n"
            "This happens — searches come in waves.\n"
        )

        if not is_featured:
            msg += (
                "\n👉 Featured businesses stay visible in slow weeks\n"
                "Reply *\"upgrade\"* to boost your listing"
            )
        else:
            msg += "\nYour Featured badge is still active 💪"

        return msg

    # ── Brand new / zero activity ──
    msg = f"Hi! This is Mira 👋\n\n"
    msg += f"Your business *{name}* is listed"
    if city:
        msg += f" in {city}"
    msg += ".\n\n"
    msg += (
        "No inquiries yet — that's normal for new listings.\n\n"
        "Quick tips:\n"
        "• Check your phone number is correct\n"
        "• Post a deal — type *\"post a deal\"*\n"
        "• Share Mira with your community 🙌\n"
    )

    if not is_featured:
        msg += "\n👉 Want to appear first? Reply *\"upgrade\"*"

    return msg


async def send_proof_messages(settings: Settings) -> dict:
    """
    Main entry point: send weekly proof messages to all business owners.

    Returns a summary dict with counts of sent/skipped/failed.
    """
    from app.services.whatsapp_service import WhatsAppService

    businesses = get_all_businesses_with_owners(settings)
    whatsapp = WhatsAppService(settings)

    sent = 0
    skipped = 0
    failed = 0

    # Group businesses by owner (one owner might have multiple businesses)
    owner_businesses: dict[str, list[dict]] = {}
    for biz in businesses:
        owner_id = biz.get("source_id", "").strip()
        if not owner_id:
            skipped += 1
            continue
        # source_id might be stored as "wa:PHONE" or just "PHONE"
        wa_id = owner_id.replace("wa:", "").strip()
        if not wa_id:
            skipped += 1
            continue
        owner_businesses.setdefault(wa_id, []).append(biz)

    logger.info(
        f"Proof messages: {len(businesses)} businesses, "
        f"{len(owner_businesses)} unique owners"
    )

    for wa_id, biz_list in owner_businesses.items():
        for biz in biz_list:
            try:
                this_week = get_inquiry_count(biz["id"], settings, days=7)
                last_week = get_inquiry_count(biz["id"], settings, days=14) - this_week

                message = build_proof_message(biz, this_week, max(last_week, 0))
                await whatsapp.send_text_message(wa_id, message)
                sent += 1

                logger.info(
                    f"Proof message sent: {biz['name']} → {wa_id} "
                    f"(this_week={this_week}, last_week={last_week})"
                )

            except Exception as e:
                logger.error(f"Failed to send proof message for {biz.get('name')}: {e}")
                failed += 1

    summary = {
        "total_businesses": len(businesses),
        "unique_owners": len(owner_businesses),
        "sent": sent,
        "skipped": skipped,
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
            .select("id, name, city, state, is_featured, source_id, category")
            .ilike("source_id", f"%{wa_id}%")
            .execute()
        )

        if not biz_result.data:
            return (
                "I couldn't find any businesses linked to your account.\n"
                "Add your business first by typing *'add my business'*."
            )

        whatsapp = WhatsAppService(settings)
        messages_sent = 0

        for biz in biz_result.data:
            this_week = get_inquiry_count(biz["id"], settings, days=7)
            last_week = get_inquiry_count(biz["id"], settings, days=14) - this_week
            message = build_proof_message(biz, this_week, max(last_week, 0))
            await whatsapp.send_text_message(wa_id, message)
            messages_sent += 1

        if messages_sent == 1:
            return ""  # Message already sent directly
        return ""  # All messages sent directly

    except Exception as e:
        logger.error(f"Failed to send single proof message for {wa_id}: {e}")
        return "Sorry, couldn't generate your weekly report right now. Try again later. 🙏"
