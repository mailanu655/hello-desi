"""
Hello Desi — WhatsApp Message Sending Service

Ported from: python-whatsapp-bot-main/app/utils/whatsapp_utils.py → send_message()

Key changes from reference:
- Uses async httpx instead of sync requests
- Settings injected via constructor (not Flask current_app)
- Proper error handling with logging
"""

import logging

import httpx

from app.utils.whatsapp_utils import get_text_message_payload, process_text_for_whatsapp
from config.settings import Settings

logger = logging.getLogger(__name__)


class WhatsAppService:
    """Handles sending messages via WhatsApp Cloud API."""

    def __init__(self, settings: Settings):
        self.settings = settings
        self.api_url = settings.whatsapp_api_url
        self.headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {settings.ACCESS_TOKEN}",
        }

    async def send_text_message(self, recipient: str, text: str) -> dict | None:
        """
        Send a text message to a WhatsApp user.

        Args:
            recipient: The recipient's WhatsApp ID (phone number with country code)
            text: The message text to send

        Returns:
            The API response dict, or None if sending failed
        """
        # Format text for WhatsApp (convert markdown bold, etc.)
        formatted_text = process_text_for_whatsapp(text)

        # Build payload
        payload = get_text_message_payload(recipient, formatted_text)

        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.post(
                    self.api_url,
                    json=payload,
                    headers=self.headers,
                )
                response.raise_for_status()

                result = response.json()
                logger.info(f"Message sent to {recipient}: {response.status_code}")
                return result

        except httpx.TimeoutException:
            logger.error(f"Timeout sending message to {recipient}")
            return None
        except httpx.HTTPStatusError as e:
            logger.error(
                f"HTTP error sending message to {recipient}: "
                f"{e.response.status_code} — {e.response.text}"
            )
            return None
        except httpx.RequestError as e:
            logger.error(f"Request error sending message to {recipient}: {e}")
            return None
