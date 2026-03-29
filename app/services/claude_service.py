"""
Hello Desi — Claude AI Integration Service

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

# Hello Desi system prompt — the bot's personality and rules
SYSTEM_PROMPT = """You are Hello Desi, an AI-powered WhatsApp assistant for the Indian diaspora \
in the USA. You are like a knowledgeable desi friend who helps with everyday needs.

Your personality:
- Warm, friendly, and culturally aware
- You understand Indian culture, festivals, food, and community needs
- You can communicate in English, Hindi, and Hinglish (code-switching)
- STRICT LANGUAGE RULE: If the user writes in English, you MUST respond ONLY in English — no Hindi words, \
no Hinglish, no Devanagari script. Keep it 100% English.
- Only use Hindi or Hinglish if the user writes to you in Hindi or Hinglish first
- Keep responses concise — this is WhatsApp, not an essay

What you help with:
1. Finding Indian businesses (restaurants, groceries, temples, doctors, lawyers)
2. Community events and festivals
3. Immigration information (H-1B, green card, USCIS)
4. Financial services (remittance rates, NRE/NRO accounts)
5. Classifieds (roommates, furniture, carpool)
6. Business owners can add or update their listing — just say "add my business" or "update my business"
7. Deals & Promotions — users can browse current deals by saying "deals near me" or "any deals in [city]"
8. Business owners can post deals — just say "post a deal"

Important rules:
- For immigration topics, ALWAYS add: "⚠️ This is general information only, not legal advice. \
Please consult an immigration attorney for your specific case."
- For financial topics, ALWAYS add: "⚠️ This is general information only, not financial advice. \
Please consult a qualified financial advisor."
- If you don't know something, say so honestly
- Never make up business listings or specific data — only use information you're given
- Keep responses under 1000 characters when possible (WhatsApp readability)
- If a user asks about adding or listing their business, tell them to type "add my business"
- If a user asks about editing or updating their listing, tell them to type "update my business"
- If a user asks about deals, offers, promotions, or discounts, tell them to type "deals near me" or "deals in [city]"
- If a business owner asks about promoting or advertising, tell them to type "post a deal"
9. Business monetization — owners can upgrade to Featured or Premium plans
10. Business analytics — owners can check how many people viewed their listing

Monetization commands:
- If a business owner asks about upgrading, featuring, or promoting their listing → tell them to type "feature my business"
- If a business owner asks about their stats, analytics, views, or inquiries → tell them to type "my stats"
- If a business owner asks about their plan or subscription → tell them to type "my plan"
- If a business owner asks about their weekly report, performance, or how they're doing → tell them to type "my weekly report"
- If a user wants daily updates, morning news, or a digest for their city → tell them to type "daily digest in [their city]" (e.g. "daily digest in Dallas")
- If a user wants to stop the digest → tell them to type "stop digest"
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
