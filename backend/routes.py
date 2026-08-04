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

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import PlainTextResponse

import whatsapp
from core.logger import logger

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


@router.get("/webhook")
def verify_webhook(request: Request):
    """Meta's one-time subscription handshake.

    Echo hub.challenge as plain text when the verify token matches. Fails
    closed when WHATSAPP_VERIFY_TOKEN is unset — otherwise anyone could
    complete the subscription and start receiving this number's messages.
    """
    if whatsapp.verify_token_matches(request.query_params):
        logger.info("[webhook] verification handshake accepted")
        return PlainTextResponse(request.query_params.get("hub.challenge", ""))

    logger.warning("[webhook] verification rejected (token mismatch or unconfigured)")
    raise HTTPException(status_code=403, detail="Verification failed")


@router.post("/webhook")
async def webhook(request: Request):
    """Every Cloud API event: messages, statuses, receipts, errors.

    Returns 200 for anything that passes signature verification, including
    events we don't act on. A non-200 makes Meta redeliver the whole batch,
    so one unrecognised event would replay every message beside it.
    """
    raw = await request.body()

    # SECURITY: fail CLOSED. An unset app secret must deny everyone rather than
    # let anyone POST forged messages straight into the concierge — that is a
    # path to putting words in a user's mouth and spending their money.
    if not whatsapp.verify_signature(raw, request.headers.get("x-hub-signature-256", "")):
        logger.warning("[webhook] rejected: invalid X-Hub-Signature-256")
        raise HTTPException(status_code=403, detail="Invalid signature")

    try:
        payload = await request.json()
    except Exception:
        logger.warning("[webhook] rejected: body was not JSON")
        raise HTTPException(status_code=400, detail="Malformed payload")

    if not isinstance(payload, dict):
        logger.warning(f"[webhook] ignoring non-object payload ({type(payload).__name__})")
        return {"status": "ignored"}

    if payload.get("object") and payload["object"] != "whatsapp_business_account":
        # Some other Meta product subscribed to this endpoint. Not ours.
        logger.info(f"[webhook] ignoring object={payload['object']!r}")
        return {"status": "ignored"}

    messages = whatsapp.parse_inbound(payload)
    statuses = whatsapp.parse_statuses(payload)
    errors = whatsapp.parse_errors(payload)
    logger.info(f"[webhook] received {len(messages)} message(s), "
                f"{len(statuses)} status(es), {len(errors)} error(s)")

    for message in messages:
        if _debug_enabled():
            logger.info(f"[webhook] 📩 {message['phone']}: {message['body']!r}")
        await _queue(message["message_id"], message["phone"], message["body"],
                     message["unsupported"])

    _record_statuses(statuses)

    for error in errors:
        # Account-level problems — number quality, template rejections. Nothing
        # to reply to; they need a human looking at Business Manager.
        logger.error(f"[webhook] Meta account error {error['code']}: {error['title']}")

    return {"status": "received"}


def _record_statuses(statuses: list) -> None:
    """Persist delivery and read receipts against the message we sent.

    Best effort: a receipt is bookkeeping, and losing one must never make us
    fail a webhook Meta would then redeliver in full.
    """
    import db

    for status in statuses:
        if not status["message_id"]:
            continue
        try:
            db.record_delivery_status(
                provider_sid=status["message_id"],
                status=status["status"],
                error_code=status["error_code"],
            )
        except Exception as e:
            logger.error(f"[webhook] could not record status: {e!r}")

        if status["status"] == "failed":
            logger.error(
                f"[webhook] delivery FAILED to {status['recipient']} "
                f"(message_id={status['message_id']}, code={status['error_code']}, "
                f"{status['error_title']})"
            )
        else:
            logger.info(
                f"[webhook] {status['status']} — {status['message_id']} "
                f"to {status['recipient']}"
            )
