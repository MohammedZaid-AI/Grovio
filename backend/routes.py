"""
HTTP surface for the AI Food Concierge.

One capability: the WhatsApp webhook. There is no dashboard, no admin panel and
no upload endpoints — WhatsApp is the entire product.

The webhook does the minimum possible work: verify the sender, persist the
inbound message, return 200 in milliseconds. All reasoning and delivery happens
in the background worker (backend/whatsapp_worker.py), because LLM and provider
calls take far longer than any webhook timeout allows.
"""
import os

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import PlainTextResponse, Response

from core.logger import logger
from whatsapp.transport import TRANSPORT

router = APIRouter()


def _debug_enabled() -> bool:
    """SECURITY: message content is personal. Log it only when asked."""
    return os.getenv("DEBUG", "").strip().lower() in ("1", "true", "yes")


def split_message(text: str, max_length: int = 1500) -> list:
    """Split a reply into WhatsApp-sized parts, preferring paragraph then word
    boundaries. Also used by the delivery worker."""
    if len(text) <= max_length:
        return [text]

    parts = []
    current_part = []
    current_length = 0

    for paragraph in text.split("\n"):
        para_len = len(paragraph) + (1 if current_part else 0)

        if current_length + para_len > max_length:
            if current_part:
                parts.append("\n".join(current_part))
                current_part = []
                current_length = 0

            if len(paragraph) > max_length:
                current_word_part = []
                word_part_len = 0
                for word in paragraph.split(" "):
                    added_len = len(word) + (1 if current_word_part else 0)
                    if word_part_len + added_len > max_length:
                        if current_word_part:
                            parts.append(" ".join(current_word_part))
                        current_word_part = [word]
                        word_part_len = len(word)
                    else:
                        current_word_part.append(word)
                        word_part_len += added_len
                if current_word_part:
                    current_part.append(" ".join(current_word_part))
                    current_length += len(current_part[-1])
            else:
                current_part.append(paragraph)
                current_length = len(paragraph)
        else:
            current_part.append(paragraph)
            current_length += para_len

    if current_part:
        parts.append("\n".join(current_part))

    return parts


async def _queue(message_id, phone, body, unsupported=False):
    """Persist one inbound message and wake its worker. Never raises — a 500
    makes the platform retry, and the loss is logged rather than silent."""
    from backend.whatsapp_worker import enqueue_and_wake

    try:
        _, is_new = await enqueue_and_wake(
            message_sid=message_id,
            phone=phone,
            body=body,
            num_media=1 if unsupported else 0,
        )
        if not is_new:
            logger.info(f"Duplicate webhook ignored (message_id={message_id})")
    except Exception as e:
        logger.error(f"webhook enqueue failed: {e}", exc_info=True)


# ----------------------------------------------------------------------
# WhatsApp Cloud API (default transport)
# ----------------------------------------------------------------------
@router.get("/webhook")
def verify_webhook(request: Request):
    """Meta's one-time subscription handshake: echo hub.challenge if the
    verify token matches. Fails closed when unconfigured."""
    params = request.query_params
    expected = os.getenv("WHATSAPP_VERIFY_TOKEN")

    if expected and params.get("hub.mode") == "subscribe" \
            and params.get("hub.verify_token") == expected:
        return PlainTextResponse(params.get("hub.challenge", ""))

    logger.warning("Webhook verification rejected (token mismatch or unconfigured)")
    raise HTTPException(status_code=403, detail="Verification failed")


@router.post("/webhook")
async def webhook(request: Request):
    """Inbound messages.

    Cloud API posts JSON signed with X-Hub-Signature-256. Twilio posts a form
    signed with X-Twilio-Signature; that path stays until the Cloud API number
    is live and is selected by WHATSAPP_TRANSPORT.
    """
    raw = await request.body()

    if TRANSPORT == "twilio":
        return await _twilio_webhook(request, raw)

    from whatsapp.cloud_api import parse_inbound, verify_signature

    # SECURITY: fail CLOSED. An unset app secret must deny everyone rather than
    # let anyone POST forged messages straight into the concierge.
    if not verify_signature(raw, request.headers.get("x-hub-signature-256", "")):
        logger.warning("Webhook rejected: invalid X-Hub-Signature-256")
        raise HTTPException(status_code=403, detail="Invalid signature")

    try:
        payload = await request.json()
    except Exception:
        logger.warning("Webhook rejected: body was not JSON")
        raise HTTPException(status_code=400, detail="Malformed payload")

    for message in parse_inbound(payload):
        if not message["phone"]:
            continue
        if _debug_enabled():
            print(f"📩 {message['phone']}: {message['body']!r}")
        await _queue(
            message["message_id"], message["phone"], message["body"], message["unsupported"]
        )

    # Always 200 — a non-200 makes Meta retry the whole batch.
    return {"status": "received"}


# ----------------------------------------------------------------------
# Twilio (legacy transport, WHATSAPP_TRANSPORT=twilio)
# ----------------------------------------------------------------------
async def _twilio_webhook(request: Request, raw: bytes):
    from twilio.request_validator import RequestValidator
    from twilio.twiml.messaging_response import MessagingResponse

    auth_token = os.getenv("TWILIO_AUTH_TOKEN")
    if not auth_token:
        logger.error("Webhook rejected: TWILIO_AUTH_TOKEN is not configured.")
        raise HTTPException(status_code=500, detail="Webhook is not configured correctly.")

    proto = request.headers.get("x-forwarded-proto", "http")
    host = request.headers.get("x-forwarded-host") or request.headers.get("host") or "localhost:8000"
    url = f"{proto}://{host}{request.url.path}"

    form = dict(await request.form())
    signature = request.headers.get("x-twilio-signature", "")

    if not RequestValidator(auth_token).validate(url, form, signature):
        # The reconstructed url must EXACTLY match the console URL. http-vs-https
        # behind a tunnel is the usual cause. No secrets logged.
        logger.warning(f"Webhook signature verification failed (url: {url})")
        raise HTTPException(status_code=403, detail="Invalid Twilio signature")

    num_media = int(form.get("NumMedia") or 0)
    phone = form.get("From", "")
    if phone:
        if _debug_enabled():
            print(f"📩 {phone}: {form.get('Body')!r}")
        await _queue(form.get("MessageSid", ""), phone, form.get("Body", ""), num_media > 0)

    return Response(content=str(MessagingResponse()), media_type="application/xml")
