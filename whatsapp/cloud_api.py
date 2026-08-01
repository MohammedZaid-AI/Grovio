"""
WhatsApp Cloud API transport (Meta official).

Replaces Twilio. Chosen over unofficial WhatsApp Web automation (OpenWA and
friends) because those violate WhatsApp's terms and get numbers banned — an
unacceptable risk for a product that *is* WhatsApp.

Environment:
    WHATSAPP_ACCESS_TOKEN      permanent system-user token
    WHATSAPP_PHONE_NUMBER_ID   the sender's phone number id
    WHATSAPP_APP_SECRET        for X-Hub-Signature-256 webhook verification
    WHATSAPP_VERIFY_TOKEN      the string echoed during webhook registration
    WHATSAPP_API_VERSION       optional, defaults below
"""
import hashlib
import hmac
import os
from collections import namedtuple

import httpx

DEFAULT_API_VERSION = "v21.0"

SendErrorClass = namedtuple("SendErrorClass", ["retryable", "code", "reason"])

# Cloud API error codes that are PERMANENT for this message — retrying only
# wastes attempts and delays the FAILED verdict.
NON_RETRYABLE_CODES = {
    100,     # invalid parameter (bad recipient / malformed payload)
    131026,  # message undeliverable (recipient not on WhatsApp)
    131047,  # re-engagement required — outside the 24h customer service window
    131051,  # unsupported message type
    132000,  # template param mismatch
    190,     # access token expired or invalid
    200,     # permission denied
    10,      # application does not have permission
}


def _config():
    token = os.getenv("WHATSAPP_ACCESS_TOKEN")
    phone_number_id = os.getenv("WHATSAPP_PHONE_NUMBER_ID")
    if not (token and phone_number_id):
        raise RuntimeError(
            "WhatsApp Cloud API is not configured. Set WHATSAPP_ACCESS_TOKEN and "
            "WHATSAPP_PHONE_NUMBER_ID."
        )
    version = os.getenv("WHATSAPP_API_VERSION", DEFAULT_API_VERSION)
    return token, phone_number_id, version


def _to_msisdn(number):
    """Cloud API wants a bare international number: no '+', no 'whatsapp:'."""
    return (number or "").replace("whatsapp:", "").replace("+", "").strip()


def verify_signature(raw_body: bytes, signature_header: str) -> bool:
    """Validate X-Hub-Signature-256.

    SECURITY: fails CLOSED. A missing app secret denies every request rather
    than silently accepting forged webhooks.
    """
    app_secret = os.getenv("WHATSAPP_APP_SECRET")
    if not app_secret or not signature_header:
        return False
    if not signature_header.startswith("sha256="):
        return False

    expected = hmac.new(
        app_secret.encode("utf-8"), raw_body, hashlib.sha256
    ).hexdigest()
    return hmac.compare_digest(expected, signature_header[len("sha256="):])


def parse_inbound(payload: dict) -> list:
    """Extract inbound text messages from a webhook payload.

    Cloud API batches events and also delivers status callbacks (delivered/read)
    through the same endpoint, so anything that is not an inbound text is
    ignored. Returns [{message_id, phone, body}].
    """
    messages = []
    for entry in payload.get("entry", []):
        for change in entry.get("changes", []):
            value = change.get("value", {})
            for message in value.get("messages", []):
                if message.get("type") != "text":
                    # Media/audio land in a later phase; record it so the worker
                    # can reply with the "can't read attachments yet" note.
                    messages.append({
                        "message_id": message.get("id", ""),
                        "phone": message.get("from", ""),
                        "body": "",
                        "unsupported": True,
                    })
                    continue
                messages.append({
                    "message_id": message.get("id", ""),
                    "phone": message.get("from", ""),
                    "body": (message.get("text") or {}).get("body", ""),
                    "unsupported": False,
                })
    return messages


def classify_send_error(exc):
    """Classify a send failure as retryable (transient) or permanent."""
    if isinstance(exc, RuntimeError):
        return SendErrorClass(False, None, "Cloud API not configured")

    if isinstance(exc, httpx.HTTPStatusError):
        status = exc.response.status_code
        code = None
        try:
            code = (exc.response.json().get("error") or {}).get("code")
        except Exception:
            pass

        if code in NON_RETRYABLE_CODES:
            return SendErrorClass(False, code, f"permanent Cloud API error {code}")
        if status in (401, 403):
            return SendErrorClass(False, code, f"authentication failure (HTTP {status})")
        if status == 429:
            return SendErrorClass(True, code, "transient rate limit (HTTP 429)")
        if 500 <= status <= 599:
            return SendErrorClass(True, code, f"transient server error (HTTP {status})")
        if 400 <= status <= 499:
            suffix = f", code {code}" if code else ""
            return SendErrorClass(False, code, f"non-retryable client error (HTTP {status}{suffix})")
        return SendErrorClass(True, code, f"Cloud API error (HTTP {status})")

    # Network failures, timeouts, connection resets.
    return SendErrorClass(True, None, f"transient network error ({type(exc).__name__})")


async def send_whatsapp(to, body):
    """Send one text message; return the provider message id.

    Raises on any failure so the worker can retry — never swallows an error,
    because a dropped reply must be visible rather than silent.
    """
    token, phone_number_id, version = _config()

    async with httpx.AsyncClient(timeout=20) as client:
        response = await client.post(
            f"https://graph.facebook.com/{version}/{phone_number_id}/messages",
            headers={"Authorization": f"Bearer {token}"},
            json={
                "messaging_product": "whatsapp",
                "recipient_type": "individual",
                "to": _to_msisdn(to),
                "type": "text",
                "text": {"preview_url": False, "body": body},
            },
        )
        response.raise_for_status()
        data = response.json()

    return (data.get("messages") or [{}])[0].get("id", "")
