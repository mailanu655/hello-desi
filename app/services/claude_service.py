"""
Hello Desi — Claude AI Integration Service

Replaces: python-whatsapp-bot-main/app/services/openai_service.py

Uses Anthropic Claude API instead of OpenAI Assistants API.
- Claude Haiku for 90% of queries (fast, cheap)
- Claude Sonnet for complex immigration/finance questions
"""

import logging

import anthropic

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
- Always respond in the same language the user writes in
- Keep responses concise — this is WhatsApp, not an essay

What you help with:
1. Finding Indian businesses (restaurants, groceries, temples, doctors, lawyers)
2. Community events and festivals
3. Immigration information (H-1B, green card, USCIS)
4. Financial services (remittance rates, NRE/NRO accounts)
5. Classifieds (roommates, furniture, carpool)

Important rules:
- For immigration topics, ALWAYS add: "⚠️ This is general information only, not legal advice. \
Please consult an immigration attorney for your specific case."
- For financial topics, ALWAYS add: "⚠️ This is general information only, not financial advice. \
Please consult a qualified financial advisor."
- If you don't know something, say so honestly
- Never make up business listings or specific data — only use information you're given
- Keep responses under 1000 characters when possible (WhatsApp readability)
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

        response = client.messages.create(
            model=model,
            max_tokens=1024,
            system=f"{SYSTEM_PROMPT}\n\nThe user's name is {name}.",
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
