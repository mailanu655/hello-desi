"""
Mira — WhatsApp Utility Functions

Ported from: python-whatsapp-bot-main/app/utils/whatsapp_utils.py

Key changes from reference:
- Removed Flask dependency (current_app)
- extract_message_data() returns sender's wa_id (not hardcoded RECIPIENT_WAID)
- Async-ready (no blocking I/O in utilities)
"""

import re


def is_valid_whatsapp_message(body: dict) -> bool:
    """
    Check if the incoming webhook payload has a valid WhatsApp message.

    WhatsApp webhooks have a deeply nested structure:
    body.entry[0].changes[0].value.messages[0]
    """
    return bool(
        body.get("object")
        and body.get("entry")
        and body["entry"][0].get("changes")
        and body["entry"][0]["changes"][0].get("value")
        and body["entry"][0]["changes"][0]["value"].get("messages")
        and body["entry"][0]["changes"][0]["value"]["messages"][0]
    )


def extract_message_data(body: dict) -> dict:
    """
    Extract wa_id, name, and message body from a validated WhatsApp webhook payload.

    FIX from reference: Returns the SENDER's wa_id, not a hardcoded RECIPIENT_WAID.
    """
    value = body["entry"][0]["changes"][0]["value"]
    contact = value["contacts"][0]
    message = value["messages"][0]

    return {
        "wa_id": contact["wa_id"],
        "name": contact["profile"]["name"],
        "message_body": message["text"]["body"],
        "message_type": message.get("type", "text"),
        "message_id": message.get("id", ""),
    }


def process_text_for_whatsapp(text: str) -> str:
    """
    Convert markdown formatting to WhatsApp-compatible formatting.

    - Removes bracket annotations like 【source】
    - Converts **bold** to *bold* (WhatsApp uses single asterisks)
    - Preserves _italic_ (same in both markdown and WhatsApp)
    """
    # Remove bracket annotations (common in AI responses with citations)
    text = re.sub(r"\【.*?\】", "", text).strip()

    # Convert double asterisks (markdown bold) to single (WhatsApp bold)
    text = re.sub(r"\*\*(.*?)\*\*", r"*\1*", text)

    return text


def get_text_message_payload(recipient: str, text: str) -> dict:
    """
    Build WhatsApp Cloud API message payload.

    Returns a dict (not JSON string) for use with httpx.
    """
    return {
        "messaging_product": "whatsapp",
        "recipient_type": "individual",
        "to": recipient,
        "type": "text",
        "text": {"preview_url": False, "body": text},
    }
