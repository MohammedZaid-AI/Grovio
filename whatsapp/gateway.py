"""
The WhatsApp transport: an HTTP hop to the Baileys gateway.

    backend  ->  POST {gateway}/send  ->  Baileys  ->  WhatsApp

This module is the ONLY thing in the backend that knows a gateway exists, and it
knows nothing about Baileys: it posts `{phone, text}` and reads back a message
id. Every WhatsApp concept — JIDs, message keys, session state — stops on the
other side of that HTTP call.

Nothing above this file changed. `backend/whatsapp_worker.py` still imports
`send_text` and `classify_send_error` from `whatsapp`, still calls
`send_text(phone, body)`, and still reads `SendErrorClass(retryable, code,
reason)` to decide retry-or-fail. Mapping the gateway's HTTP statuses onto that
verdict is the whole job here.

⚠️ Baileys is an UNOFFICIAL WhatsApp Web protocol client, not the Meta WhatsApp
Business Cloud API. WhatsApp can ban the number. Use a spare one, never a number
tied to a business account.
"""
import base64
import os
from collections import namedtuple

import httpx

from core.logger import logger

SendErrorClass = namedtuple("SendErrorClass", "retryable code reason")

# WhatsApp's own per-message ceiling. The worker splits replies well below this.
MAX_MESSAGE_LENGTH = 4096

_TIMEOUT = httpx.Timeout(30.0, connect=10.0)


class NotConfigured(RuntimeError):
    """No shared secret. Fails closed rather than posting to an unauthenticated
    port or letting messages silently vanish."""


def gateway_url() -> str:
    return (os.getenv("WHATSAPP_GATEWAY_URL") or "http://localhost:8100").rstrip("/")


def _secret() -> str:
    return os.getenv("WHATSAPP_GATEWAY_SECRET") or ""


def is_configured() -> bool:
    return bool(_secret())


def _require_config():
    if not _secret():
        raise NotConfigured(
            "WHATSAPP_GATEWAY_SECRET is not set, so the gateway would reject "
            "every send. Set it to the same value the gateway has."
        )


def canonical_phone(number) -> str:
    """The one phone format this system stores and compares.

    A bare international MSISDN: no '+', no 'whatsapp:' prefix, no spaces, no
    JID suffix. Unchanged from the previous transport on purpose — one human is
    one key, whatever wrote the row. A second format means the same person
    becomes a second user with no memory, no order history and no linked
    account, and stops matching AUTHORIZED_PHONES.
    """
    if not number:
        return ""
    text = str(number).strip()
    for prefix in ("whatsapp:", "tel:"):
        if text.lower().startswith(prefix):
            text = text[len(prefix):]
    # Drop the JID suffix AND the multi-device suffix before taking digits.
    # "917795871481:12@s.whatsapp.net" must be 917795871481, not
    # 91779587148112 — a corrupted key silently becomes a second user with no
    # memory, no order history and no place on the ordering allowlist.
    text = text.split("@")[0].split(":")[0]
    return "".join(c for c in text if c.isdigit())


def classify_send_error(exc) -> SendErrorClass:
    """Retryable or permanent, and why.

    This is the contract `whatsapp_worker._send_with_retry` reads: a
    non-retryable verdict fails the message immediately and records `code`;
    a retryable one gets MAX_SEND_ATTEMPTS with backoff.

    The gateway maps its own failures onto HTTP for exactly this purpose:
    503 means WhatsApp is not connected *right now*, which the durable queue
    exists to ride out; 401 means our secret is wrong, which no retry can fix.
    """
    if isinstance(exc, NotConfigured):
        return SendErrorClass(False, None, "gateway secret is not configured")

    if isinstance(exc, httpx.HTTPStatusError):
        status = exc.response.status_code
        if status == 401:
            return SendErrorClass(
                False, status,
                "gateway rejected our secret (HTTP 401) — WHATSAPP_GATEWAY_SECRET "
                "differs between the backend and the gateway",
            )
        if status == 400:
            return SendErrorClass(
                False, status,
                "gateway refused the message (HTTP 400) — bad recipient or empty text",
            )
        if status == 503:
            return SendErrorClass(
                True, status,
                "WhatsApp is not connected yet (HTTP 503) — the session is "
                "reconnecting, or the QR has not been scanned",
            )
        if 500 <= status <= 599:
            return SendErrorClass(True, status, f"gateway error (HTTP {status})")
        if 400 <= status <= 499:
            return SendErrorClass(False, status, f"client error (HTTP {status})")
        return SendErrorClass(True, status, f"gateway error (HTTP {status})")

    if isinstance(exc, httpx.TimeoutException):
        return SendErrorClass(True, None, "timed out talking to the gateway")

    # The gateway process is down or restarting. Retryable — the durable queue
    # exists precisely so this costs nobody their message.
    return SendErrorClass(True, None, f"gateway unreachable ({type(exc).__name__})")


async def _post(path: str, payload: dict, *, describe: str) -> dict:
    _require_config()
    url = f"{gateway_url()}{path}"
    async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
        response = await client.post(
            url, json=payload, headers={"X-Gateway-Secret": _secret()},
        )
    if response.status_code >= 400:
        # The gateway's own words, for LOGS only. The secret travels in a
        # header and is never echoed in a body, so nothing sensitive is here.
        logger.error(
            f"[whatsapp] {describe} failed: HTTP {response.status_code} "
            f"{response.text[:200]}"
        )
        response.raise_for_status()
    try:
        return response.json()
    except ValueError:
        return {}


async def send_text(to: str, body: str) -> str:
    """Send one text message. Returns the WhatsApp message id, or "".

    Signature unchanged from the previous transport: the worker calls this as
    `send_text(phone, body)` and stores the returned id as `provider_sid`.
    """
    recipient = canonical_phone(to)
    data = await _post("/send", {"phone": recipient, "text": body},
                       describe=f"send to {recipient}")
    message_id = str(data.get("message_id") or "")
    # The recipient, the id and the length — never the message body.
    logger.info(f"[whatsapp] sent text to {recipient} — "
                f"message_id={message_id} ({len(body)} chars)")
    return message_id


async def send_audio(to: str, audio: bytes, mimetype: str = "audio/ogg; codecs=opus") -> str:
    """Send a voice note. Returns the WhatsApp message id, or "".

    Base64 over the same authenticated /send route as text — one endpoint, one
    secret, one set of error semantics, so `classify_send_error` keeps working
    unchanged. The backend still never sees a Baileys object; `ptt: true`, the
    flag that makes WhatsApp render this as a voice note rather than a file
    attachment, is the gateway's business.
    """
    if not audio:
        raise ValueError("no audio to send")
    recipient = canonical_phone(to)
    data = await _post(
        "/send",
        {"phone": recipient, "audio": base64.b64encode(audio).decode(),
         "mimetype": mimetype},
        describe=f"voice note to {recipient}",
    )
    message_id = str(data.get("message_id") or "")
    logger.info(f"[whatsapp] sent voice note to {recipient} — "
                f"message_id={message_id} ({len(audio)} bytes)")
    return message_id


# Images, documents and templates are capabilities the gateway does not expose
# and nothing above this layer asks for. Declared so an accidental call fails
# LOUDLY here rather than silently doing nothing, and so the gap is documented
# rather than faked.
async def send_image(to: str, link: str, caption: str = None) -> str:
    raise NotImplementedError(
        "Sending images needs a media route on the gateway. The concierge "
        "sends text only."
    )


async def send_document(to: str, link: str, filename: str = None,
                        caption: str = None) -> str:
    raise NotImplementedError(
        "Sending documents needs a media route on the gateway. The concierge "
        "sends text only."
    )


async def send_template(to: str, name: str, language: str = "en_US",
                        components: list = None) -> str:
    raise NotImplementedError(
        "Templates are a Meta Business feature. They do not exist on a WhatsApp "
        "Web session, and there is no 24-hour window to reopen."
    )


async def mark_read(message_id: str) -> bool:
    """Read receipts need the chat JID, which stays inside the gateway by
    design. Best effort, and never allowed to affect a reply."""
    return False


async def start() -> None:
    """Nothing to start — the gateway is its own process. This only reports
    whether it is reachable, because the symptom otherwise is total silence."""
    if not is_configured():
        logger.error(
            "[whatsapp] WHATSAPP_GATEWAY_SECRET is not set. This FAILS CLOSED "
            "both ways: inbound messages are rejected and every send fails. "
            "See .env.example."
        )
        return
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            health = await client.get(f"{gateway_url()}/health")
        logger.info(f"[whatsapp] gateway at {gateway_url()} — {health.json()}")
    except Exception as e:
        logger.warning(
            f"[whatsapp] gateway at {gateway_url()} is not answering "
            f"({type(e).__name__}). Start it with `npm start` in "
            f"whatsapp-gateway/ — replies queue until it is up."
        )


async def stop() -> None:
    return None
