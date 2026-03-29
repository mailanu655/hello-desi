"""
Mira — Claude AI Integration Service

Replaces: python-whatsapp-bot-main/app/services/openai_service.py

Uses Anthropic Claude API instead of OpenAI Assistants API.
- Claude Haiku for 90% of queries (fast, cheap)
- Claude Sonnet for complex immigration/finance questions
"""

import logging

import anthropic

from app.services.business_service import search_businesses, format_businesses_for_prompt
from app.utils.whatsapp_utils import process_text_for_whatsapp
from config.settings import Settings

logger = logging.getLogger(__name__)

# Mira system prompt — the bot's personality and voice
SYSTEM_PROMPT = """You are Mira — a smart, friendly desi friend on WhatsApp who helps the Indian \
community in the USA find what they need fast.

YOUR VOICE:
- Friendly 😊, helpful 🤝, slightly desi 🇮🇳, clear & quick
- Short messages — no long paragraphs, this is WhatsApp
- Use emojis lightly (not every sentence)
- Always give options when possible
- Sound conversational, not robotic

SIGNATURE PHRASES (use these naturally):
- "Got you 👍"
- "Here are some good options 👇"
- "Want more like this?"
- "Try this 👉 …"
- "Found something useful? Share with your group 🙌"

LANGUAGE RULES:
- If the user writes in English → respond ONLY in English, no Hindi/Hinglish
- If the user writes in Hindi or Hinglish → you can match their style
- Keep responses under 800 characters when possible

WHAT YOU HELP WITH:
1. Finding Indian businesses — groceries, restaurants, tiffins, babysitters, doctors, lawyers, CPAs, temples
2. Deals & promotions — "deals near me" or "deals in [city]"
3. Community events and festivals
4. Immigration info (H-1B, green card, USCIS)
5. Financial services (remittance, NRE/NRO)
6. Classifieds (roommates, furniture, carpool)

WHEN SHOWING RESULTS:
- Lead with "Here are some good options 👇" or "Got you 👍"
- Show 3-5 options max
- End with "👉 Want directions or phone number?" or "Want more like this?"

WHEN NO RESULTS:
- Say "I couldn't find exact matches 😅"
- Suggest: "Try a nearby area or different keyword"

COMMANDS TO GUIDE USERS:
- Adding a business → "add my business"
- Updating a listing → "update my business"
- Browsing deals → "deals near me" or "deals in [city]"
- Posting a deal → "post a deal"
- Upgrading listing → "feature my business"
- Business stats → "my stats"
- Subscription info → "my plan"
- Weekly report → "my weekly report"
- Daily updates → "daily digest in [city]" (e.g. "daily digest in Columbus")
- Stop digest → "stop digest"

IMPORTANT RULES:
- Immigration topics → add: "⚠️ General info only — please consult an immigration attorney."
- Financial topics → add: "⚠️ General info only — please consult a financial advisor."
- Never make up business listings — only use data you're given
- If you don't know something, say so honestly
"""


async def generate_response(
    message: str,
    wa_id: str,
    name: str,
    settings: Settings,
    conversation_history: list[dict] | None = None,
) -> str:
    """
    Generate an AI response using Claude.

    Args:
        message: The user's message text
        wa_id: User's WhatsApp ID (for future context retrieval)
        name: User's display name
        settings: Application settings with API keys
        conversation_history: Optional list of previous messages for context

    Returns:
        The formatted response string
    """
    try:
        client = anthropic.Anthropic(api_key=settings.ANTHROPIC_API_KEY)

        # Build messages array with conversation history
        messages = []

        if conversation_history:
            messages.extend(conversation_history)

        messages.append({"role": "user", "content": message})

        # Use Haiku for most queries (fast + cheap)
        model = "claude-haiku-4-5-20251001"

        # Escalate to Sonnet for complex topics
        complex_keywords = [
            "immigration", "visa", "h1b", "h-1b", "green card", "uscis",
            "eb2", "eb-2", "eb3", "eb-3", "i-485", "i-140", "i-130",
            "nre", "nro", "tax", "investment", "legal",
        ]
        if any(kw in message.lower() for kw in complex_keywords):
            model = "claude-sonnet-4-5-20241022"
            logger.info(f"Escalating to Sonnet for complex query from {wa_id}")

        # Look up relevant businesses from our database
        business_context = ""
        try:
            results = search_businesses(message, settings, limit=5)
            if results:
                business_context = format_businesses_for_prompt(results)
                logger.info(f"Found {len(results)} businesses for query from {wa_id}")
                # Log inquiries for monetization tracking
                try:
                    from app.services.monetization_service import log_inquiry
                    log_inquiry(results, wa_id, "search", message, settings)
                except Exception as inq_err:
                    logger.warning(f"Inquiry logging failed: {inq_err}")
        except Exception as e:
            logger.warning(f"Business lookup failed for {wa_id}: {e}")

        # Look up relevant deals
        deals_context = ""
        try:
            from app.services.deals_service import search_deals, format_deals_for_prompt
            deals = search_deals(message, settings, limit=3)
            if deals:
                deals_context = format_deals_for_prompt(deals)
                logger.info(f"Found {len(deals)} deals for query from {wa_id}")
        except Exception as e:
            logger.warning(f"Deal lookup failed for {wa_id}: {e}")

        system_msg = f"{SYSTEM_PROMPT}\n\nThe user's name is {name}.{business_context}{deals_context}"

        response = client.messages.create(
            model=model,
            max_tokens=1024,
            system=system_msg,
            messages=messages,
        )

        response_text = response.content[0].text
        formatted = process_text_for_whatsapp(response_text)

        logger.info(
            f"Claude response for {wa_id} (model={model}): "
            f"{len(formatted)} chars, "
            f"input_tokens={response.usage.input_tokens}, "
            f"output_tokens={response.usage.output_tokens}"
        )

        return formatted

    except anthropic.APIError as e:
        logger.error(f"Claude API error for {wa_id}: {e}")
        return (
            "Sorry, I'm having trouble right now. Please try again in a moment! 🙏"
        )
    except Exception as e:
        logger.error(f"Unexpected error generating response for {wa_id}: {e}")
        return (
            "Oops, something went wrong on my end. Please try again! 🙏"
        )
