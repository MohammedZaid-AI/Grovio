"""
HTTP surface for the AI Food Concierge.

There is exactly one route: the WhatsApp webhook. There is no dashboard, no
admin panel, no upload endpoints — WhatsApp is the entire product.

The webhook does the minimum possible work: verify the sender, persist the
inbound message, and return 200 in milliseconds. All reasoning and delivery
happens in the background worker (see backend/whatsapp_worker.py), because the
LLM + provider calls take far longer than any webhook timeout allows.
"""
from fastapi import APIRouter, Form, Request, HTTPException
from fastapi.responses import Response
import os

from twilio.twiml.messaging_response import MessagingResponse
from twilio.request_validator import RequestValidator

from core.logger import logger

router = APIRouter()

MEDIA_UNSUPPORTED_MESSAGE = (
    "I can't read attachments yet — tell me what you're in the mood for and "
    "I'll take it from there."
)


def _debug_enabled() -> bool:
    """SECURITY (L-3): message content can be personal. Log it only on request."""
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


def whatsapp_reply(message):
    """Build the webhook's TwiML response. Normally empty — replies are
    delivered out-of-band by the worker."""
    if _debug_enabled():
        print(f"\n[DEBUG_FINAL_OUTPUT] {repr(message)}\n")

    twiml = MessagingResponse()
    for msg in (message if isinstance(message, list) else [message]):
        twiml.message(msg)

    return Response(content=str(twiml), media_type="application/xml")


@router.post("/webhook")
async def webhook(
    request: Request,
    Body: str = Form(""),
    NumMedia: int = Form(0),
    From: str = Form(""),
    MessageSid: str = Form(""),
):
    # SECURITY (M-2): fail CLOSED. A missing auth token must never silently skip
    # verification — that would let anyone POST forged messages straight into
    # the conversation engine.
    auth_token = os.getenv("TWILIO_AUTH_TOKEN")
    if not auth_token:
        logger.error("Webhook rejected: TWILIO_AUTH_TOKEN is not configured.")
        raise HTTPException(status_code=500, detail="Webhook is not configured correctly.")

    validator = RequestValidator(auth_token)
    signature = request.headers.get("x-twilio-signature", "")
    proto = request.headers.get("x-forwarded-proto", "http")
    host = request.headers.get("x-forwarded-host") or request.headers.get("host") or "localhost:8000"
    url = f"{proto}://{host}{request.url.path}"

    params = dict(await request.form())

    if not validator.validate(url, params, signature):
        # The reconstructed url must EXACTLY match the console URL (scheme + host
        # + path). http-vs-https behind a tunnel is the usual cause. No secrets.
        logger.warning(f"Webhook signature verification failed (url: {url})")
        raise HTTPException(status_code=403, detail="Invalid Twilio signature")

    if _debug_enabled():
        print(f"📩 {From}: {Body!r} (media={NumMedia})")

    if NumMedia > 0:
        return whatsapp_reply(MEDIA_UNSUPPORTED_MESSAGE)

    # Persist + return immediately; the worker does the slow part and delivers
    # the reply itself. Never fail the webhook — a 500 makes Twilio retry.
    from backend.whatsapp_worker import enqueue_and_wake

    try:
        inbound_id, is_new = await enqueue_and_wake(
            message_sid=MessageSid,
            phone=From,
            body=Body,
            num_media=NumMedia,
        )
        if not is_new:
            logger.info(f"Duplicate webhook ignored (inbound_id={inbound_id})")
    except Exception as e:
        logger.error(f"webhook enqueue failed: {e}", exc_info=True)

    return whatsapp_reply([])
