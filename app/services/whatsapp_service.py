"""
Mira — WhatsApp Message Sending Service

Ported from: python-whatsapp-bot-main/app/utils/whatsapp_utils.py → send_message()

Key changes from reference:
- Uses async httpx instead of sync requests
- Settings injected via constructor (not Flask current_app)
- Retry logic with exponential backoff (3 attempts)
- Proper error handling with logging
"""

import asyncio
import logging

import httpx

from app.utils.whatsapp_utils import get_text_message_payload, process_text_for_whatsapp
from config.settings import Settings

logger = logging.getLogger(__name__)

# Retry configuration
MAX_RETRIES = 3
BASE_BACKOFF = 1  # seconds


class WhatsAppService:
    """Handles sending messages via WhatsApp Cloud API."""

    def __init__(self, settings: Settings, request_id: str = ""):
        self.settings = settings
        self.api_url = settings.whatsapp_api_url
        self.headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {settings.ACCESS_TOKEN}",
        }
        self.request_id = request_id

    def _log_prefix(self) -> str:
        return f"[{self.request_id}] " if self.request_id else ""

    async def send_text_message(self, recipient: str, text: str) -> dict | None:
        """
        Send a text message to a WhatsApp user with retry logic.

        Retries up to MAX_RETRIES times with exponential backoff on transient failures.

        Args:
            recipient: The recipient's WhatsApp ID (phone number with country code)
            text: The message text to send

        Returns:
            The API response dict, or None if all attempts failed
        """
        prefix = self._log_prefix()

        # Format text for WhatsApp (convert markdown bold, etc.)
        formatted_text = process_text_for_whatsapp(text)

        # Build payload
        payload = get_text_message_payload(recipient, formatted_text)

        last_error = None
        for attempt in range(MAX_RETRIES):
            try:
                async with httpx.AsyncClient(timeout=10.0) as client:
                    response = await client.post(
                        self.api_url,
                        json=payload,
                        headers=self.headers,
                    )
                    response.raise_for_status()

                    result = response.json()
                    logger.info(
                        f"{prefix}Message sent to {recipient}: {response.status_code}"
                        + (f" (attempt {attempt + 1})" if attempt > 0 else "")
                    )
                    return result

            except httpx.TimeoutException as e:
                last_error = e
                logger.warning(
                    f"{prefix}Timeout sending to {recipient} "
                    f"(attempt {attempt + 1}/{MAX_RETRIES})"
                )
            except httpx.HTTPStatusError as e:
                last_error = e
                status = e.response.status_code
                # Don't retry on client errors (4xx) except 429 (rate limit)
                if 400 <= status < 500 and status != 429:
                    logger.error(
                        f"{prefix}HTTP {status} sending to {recipient} — "
                        f"not retrying: {e.response.text}"
                    )
                    return None
                logger.warning(
                    f"{prefix}HTTP {status} sending to {recipient} "
                    f"(attempt {attempt + 1}/{MAX_RETRIES}): {e.response.text}"
                )
            except httpx.RequestError as e:
                last_error = e
                logger.warning(
                    f"{prefix}Request error sending to {recipient} "
                    f"(attempt {attempt + 1}/{MAX_RETRIES}): {e}"
                )

            # Exponential backoff before retry
            if attempt < MAX_RETRIES - 1:
                wait = BASE_BACKOFF * (2 ** attempt)
                await asyncio.sleep(wait)

        logger.error(
            f"{prefix}All {MAX_RETRIES} attempts failed sending to {recipient}: "
            f"{last_error}"
        )
        return None
