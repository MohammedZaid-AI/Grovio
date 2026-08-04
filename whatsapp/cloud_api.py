"""
WhatsApp Business Cloud API — the messaging layer.

This is the ONLY module that knows Meta exists. Everything above it calls
`send_text`, `mark_read` and friends; nothing above it builds a Graph URL,
holds the access token, or parses a Meta payload.

Chosen over unofficial WhatsApp Web automation (OpenWA and friends) because
those violate WhatsApp's terms and get numbers banned — an unacceptable risk
for a product that *is* WhatsApp.

There is deliberately no `MessagingProvider` base class. One provider needs no
polymorphism, and the repo's rule against single-implementation abstractions
applies here; the module boundary IS the abstraction. A second provider would
mean extracting a protocol, not that one should have existed all along.

Environment
    WHATSAPP_ACCESS_TOKEN      system-user token (never logged)
    WHATSAPP_PHONE_NUMBER_ID   the sender's phone number id
    WHATSAPP_APP_SECRET        X-Hub-Signature-256 verification. FAILS CLOSED.
    WHATSAPP_VERIFY_TOKEN      echoed during webhook registration
    WHATSAPP_APP_ID            optional; for reference in logs and tooling
    META_API_VERSION           optional, defaults below
"""
import hashlib
import hmac
import os
from collections import namedtuple

import httpx

from core.logger import logger

DEFAULT_API_VERSION = "v23.0"
SEND_TIMEOUT = 20

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


class NotConfigured(RuntimeError):
    """Credentials are missing. A configuration error, never an outage."""


# ----------------------------------------------------------------------
# Configuration
# ----------------------------------------------------------------------
def api_version() -> str:
    return os.getenv("META_API_VERSION", DEFAULT_API_VERSION).strip() or DEFAULT_API_VERSION


def _config():
    token = os.getenv("WHATSAPP_ACCESS_TOKEN")
    phone_number_id = os.getenv("WHATSAPP_PHONE_NUMBER_ID")
    if not (token and phone_number_id):
        raise NotConfigured(
            "WhatsApp Cloud API is not configured. Set WHATSAPP_ACCESS_TOKEN and "
            "WHATSAPP_PHONE_NUMBER_ID."
        )
    return token, phone_number_id, api_version()


def is_configured() -> bool:
    return bool(os.getenv("WHATSAPP_ACCESS_TOKEN") and os.getenv("WHATSAPP_PHONE_NUMBER_ID"))


def canonical_phone(number) -> str:
    """The one phone format this system stores and compares.

    A bare international MSISDN: no '+', no 'whatsapp:' prefix, no spaces —
    exactly what the Cloud API sends and expects. Everything entering the
    system goes through here so one human is always one key, whatever wrote
    the row.
    """
    cleaned = (number or "").strip().replace("whatsapp:", "").replace("+", "")
    return "".join(c for c in cleaned if c.isdigit())


# ----------------------------------------------------------------------
# Security
# ----------------------------------------------------------------------
def verify_signature(raw_body: bytes, signature_header: str) -> bool:
    """Validate X-Hub-Signature-256.

    SECURITY: fails CLOSED. A missing app secret denies every request rather
    than silently accepting forged webhooks — anyone who can POST here can put
    words in a user's mouth and spend their money.
    """
    app_secret = os.getenv("WHATSAPP_APP_SECRET")
    if not app_secret or not signature_header:
        return False
    if not signature_header.startswith("sha256="):
        return False

    expected = hmac.new(app_secret.encode("utf-8"), raw_body, hashlib.sha256).hexdigest()
    # Constant-time: a fast reject leaks the correct prefix byte by byte.
    return hmac.compare_digest(expected, signature_header[len("sha256="):])


def verify_token_matches(params) -> bool:
    """The GET handshake. Fails closed when WHATSAPP_VERIFY_TOKEN is unset —
    otherwise anyone could complete the subscription."""
    expected = os.getenv("WHATSAPP_VERIFY_TOKEN")
    return bool(
        expected
        and params.get("hub.mode") == "subscribe"
        and params.get("hub.verify_token") == expected
    )


# ----------------------------------------------------------------------
# Inbound parsing
# ----------------------------------------------------------------------
def _listof(value) -> list:
    """A list, or nothing. Iterating a non-list from an untrusted body raises
    inside the request handler — which Meta reads as a failure and redelivers."""
    return value if isinstance(value, list) else []


def _entries(payload):
    """Walk to the `value` objects, tolerating anything malformed.

    Webhook payloads are UNTRUSTED input. Every level is type-checked rather
    than indexed, so a hostile or simply new-shaped body yields nothing instead
    of raising inside the request handler.
    """
    if not isinstance(payload, dict):
        return
    for entry in _listof(payload.get("entry")):
        if not isinstance(entry, dict):
            continue
        for change in _listof(entry.get("changes")):
            if not isinstance(change, dict):
                continue
            value = change.get("value")
            if isinstance(value, dict):
                yield value


def parse_inbound(payload: dict) -> list:
    """Inbound user messages. Returns [{message_id, phone, body, unsupported}].

    Non-text messages are kept with `unsupported=True` rather than dropped, so
    the worker can say "I can't read attachments yet" instead of going silent —
    a silent bot reads as broken.
    """
    messages = []
    for value in _entries(payload):
        for message in _listof(value.get("messages")):
            if not isinstance(message, dict):
                continue
            phone = canonical_phone(message.get("from"))
            if not phone:
                continue

            kind = message.get("type")
            if kind == "text":
                body = (message.get("text") or {}).get("body", "")
                unsupported = False
            elif kind == "interactive":
                # Button and list replies arrive here. Treat the user's chosen
                # title as what they said — it is.
                body = _interactive_text(message.get("interactive"))
                unsupported = not body
            else:
                body, unsupported = "", True

            messages.append({
                "message_id": str(message.get("id") or ""),
                "phone": phone,
                "body": body,
                "unsupported": unsupported,
                "type": kind,
            })
    return messages


def _interactive_text(interactive) -> str:
    """The text of a button or list reply, or "" if we can't read it."""
    if not isinstance(interactive, dict):
        return ""
    for key in ("button_reply", "list_reply"):
        reply = interactive.get(key)
        if isinstance(reply, dict):
            return str(reply.get("title") or reply.get("id") or "")
    return ""


def parse_statuses(payload: dict) -> list:
    """Delivery and read receipts.

    Returns [{message_id, status, recipient, timestamp, error_code, error_title}].
    `status` is Meta's own vocabulary: sent, delivered, read, failed.
    """
    statuses = []
    for value in _entries(payload):
        for status in _listof(value.get("statuses")):
            if not isinstance(status, dict):
                continue
            errors = _listof(status.get("errors"))
            first = errors[0] if errors and isinstance(errors[0], dict) else {}
            statuses.append({
                "message_id": str(status.get("id") or ""),
                "status": str(status.get("status") or "").lower(),
                "recipient": canonical_phone(status.get("recipient_id")),
                "timestamp": status.get("timestamp"),
                "error_code": first.get("code"),
                "error_title": first.get("title") or first.get("message"),
            })
    return statuses


def parse_errors(payload: dict) -> list:
    """Account-level errors Meta reports outside any specific message."""
    out = []
    for value in _entries(payload):
        for error in _listof(value.get("errors")):
            if isinstance(error, dict):
                out.append({"code": error.get("code"),
                            "title": error.get("title") or error.get("message")})
    return out


# ----------------------------------------------------------------------
# Error classification
# ----------------------------------------------------------------------
def classify_send_error(exc) -> SendErrorClass:
    """Retryable (transient) or permanent, and why.

    The distinction decides whether the worker tries again or marks the message
    FAILED. Getting it wrong either burns attempts on a hopeless send or drops
    a message that would have gone through a second later.
    """
    if isinstance(exc, NotConfigured):
        return SendErrorClass(False, None, "Cloud API not configured")

    if isinstance(exc, httpx.HTTPStatusError):
        status = exc.response.status_code
        code = _error_code(exc.response)

        if code in NON_RETRYABLE_CODES:
            return SendErrorClass(False, code, f"permanent Cloud API error {code}")
        if status == 401:
            return SendErrorClass(False, code, "access token expired or invalid (HTTP 401)")
        if status == 403:
            return SendErrorClass(False, code, "permission denied (HTTP 403)")
        if status == 429:
            return SendErrorClass(True, code, "rate limited (HTTP 429)")
        if 500 <= status <= 599:
            return SendErrorClass(True, code, f"Meta server error (HTTP {status})")
        if 400 <= status <= 499:
            suffix = f", code {code}" if code else ""
            return SendErrorClass(False, code, f"client error (HTTP {status}{suffix})")
        return SendErrorClass(True, code, f"Cloud API error (HTTP {status})")

    if isinstance(exc, httpx.TimeoutException):
        return SendErrorClass(True, None, "timed out talking to Meta")

    # Network failures, connection resets.
    return SendErrorClass(True, None, f"network error ({type(exc).__name__})")


def _error_code(response):
    try:
        return (response.json().get("error") or {}).get("code")
    except Exception:
        return None


def _error_detail(response) -> str:
    """Meta's own words, for LOGS ONLY. Never shown to a user."""
    try:
        error = response.json().get("error") or {}
    except Exception:
        return response.text[:300]
    bits = [f"code={error.get('code')}", f"type={error.get('type')}"]
    if error.get("message"):
        bits.append(f"message={error['message']}")
    if error.get("error_subcode"):
        bits.append(f"subcode={error['error_subcode']}")
    if (error.get("error_data") or {}).get("details"):
        bits.append(f"details={error['error_data']['details']}")
    return " ".join(str(b) for b in bits)[:500]


# ----------------------------------------------------------------------
# Outbound
# ----------------------------------------------------------------------
async def _post(payload: dict, *, describe: str) -> dict:
    """POST to the messages endpoint. Raises on failure so the worker retries.

    Never swallows an error: a dropped reply must be visible, not silent.
    """
    token, phone_number_id, version = _config()
    url = f"https://graph.facebook.com/{version}/{phone_number_id}/messages"

    async with httpx.AsyncClient(timeout=SEND_TIMEOUT) as client:
        response = await client.post(
            url, headers={"Authorization": f"Bearer {token}"}, json=payload
        )

    if response.status_code >= 400:
        # The full Meta body, so a failure is diagnosable. The token is in the
        # request headers and is never part of this.
        logger.error(
            f"[whatsapp] {describe} failed: HTTP {response.status_code} "
            f"{_error_detail(response)}"
        )
        response.raise_for_status()

    return response.json() if response.content else {}


def _sent_id(data: dict) -> str:
    messages = data.get("messages")
    if isinstance(messages, list) and messages and isinstance(messages[0], dict):
        return str(messages[0].get("id") or "")
    return ""


async def send_text(to: str, body: str) -> str:
    """Send a text message. Returns Meta's message id."""
    recipient = canonical_phone(to)
    message_id = _sent_id(await _post({
        "messaging_product": "whatsapp",
        "recipient_type": "individual",
        "to": recipient,
        "type": "text",
        "text": {"preview_url": False, "body": body},
    }, describe=f"send_text to {recipient}"))
    logger.info(f"[whatsapp] sent text to {recipient} — message_id={message_id} "
                f"({len(body)} chars)")
    return message_id


async def send_image(to: str, link: str, caption: str = None) -> str:
    """Send an image by public URL."""
    recipient = canonical_phone(to)
    image = {"link": link}
    if caption:
        image["caption"] = caption
    message_id = _sent_id(await _post({
        "messaging_product": "whatsapp", "recipient_type": "individual",
        "to": recipient, "type": "image", "image": image,
    }, describe=f"send_image to {recipient}"))
    logger.info(f"[whatsapp] sent image to {recipient} — message_id={message_id}")
    return message_id


async def send_document(to: str, link: str, filename: str = None,
                        caption: str = None) -> str:
    """Send a document by public URL."""
    recipient = canonical_phone(to)
    document = {"link": link}
    if filename:
        document["filename"] = filename
    if caption:
        document["caption"] = caption
    message_id = _sent_id(await _post({
        "messaging_product": "whatsapp", "recipient_type": "individual",
        "to": recipient, "type": "document", "document": document,
    }, describe=f"send_document to {recipient}"))
    logger.info(f"[whatsapp] sent document to {recipient} — message_id={message_id}")
    return message_id


async def send_template(to: str, name: str, language: str = "en_US",
                        components: list = None) -> str:
    """Send a pre-approved template.

    The only way to open a conversation outside the 24-hour customer service
    window — error 131047 is Meta telling you to use one of these.
    """
    recipient = canonical_phone(to)
    template = {"name": name, "language": {"code": language}}
    if components:
        template["components"] = components
    message_id = _sent_id(await _post({
        "messaging_product": "whatsapp", "recipient_type": "individual",
        "to": recipient, "type": "template", "template": template,
    }, describe=f"send_template {name} to {recipient}"))
    logger.info(f"[whatsapp] sent template {name!r} to {recipient} — "
                f"message_id={message_id}")
    return message_id


async def mark_read(message_id: str) -> bool:
    """Show the user their message was read.

    Best effort: a failed read receipt must never affect the reply, so this
    logs and returns False rather than raising.

    A typing indicator goes on this same call — add
    `"typing_indicator": {"type": "text"}` when we want one.
    """
    if not message_id:
        return False
    try:
        await _post({
            "messaging_product": "whatsapp",
            "status": "read",
            "message_id": message_id,
        }, describe=f"mark_read {message_id}")
        return True
    except Exception as e:
        logger.info(f"[whatsapp] mark_read({message_id}) failed, ignoring: {e!r}")
        return False
