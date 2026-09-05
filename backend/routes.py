"""
HTTP surface for the AI Food Concierge.

One capability: POST /webhook/inbound, where the Baileys WhatsApp gateway
delivers messages. There is no dashboard, no admin panel and no upload
endpoints — WhatsApp is the entire product.

The route does the minimum possible work: authenticate the gateway, persist the
inbound message, return 200 in milliseconds. All reasoning and delivery happens
in the background worker (backend/whatsapp_worker.py), because LLM and provider
calls take far longer than the gateway's timeout allows.
"""
import hmac
import os

from fastapi import APIRouter, HTTPException, Request

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


# Said to the user when speech cannot be handled. Plain, never technical.
VOICE_UNAVAILABLE = (
    "I can't listen to voice notes right now — could you type it instead? "
    "Just a few words is plenty 🙂"
)
VOICE_UNREADABLE = (
    "Sorry, I couldn't quite catch that one. Mind sending it again, or typing it?"
)


async def _queue(message_id, phone, body, unsupported=False, voice=False,
                 language=None, note=None):
    """Persist one inbound message and wake its worker. Never raises — a 500
    makes the platform retry, and the loss is logged rather than silent."""
    import db
    from backend.whatsapp_worker import enqueue_and_wake

    if note:
        # Speech could not be handled. Answer in text through the same outbound
        # queue as any reply, and do not trouble the concierge with it.
        from backend.whatsapp_worker import deliver

        try:
            await deliver(phone, note)
        except Exception:
            logger.error("[webhook] could not queue a voice fallback", exc_info=True)
        return

    if voice:
        # Recorded BEFORE the worker is woken, or the turn could be answered
        # before we know it should be spoken.
        db.note_voice_turn(phone, language)

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


def _gateway_secret_ok(request: Request) -> bool:
    """The gateway is trusted only because it proves it holds the shared secret.

    SECURITY: fails CLOSED. An unset WHATSAPP_GATEWAY_SECRET denies every
    request rather than accepting forged messages — anyone who can POST here
    could otherwise put words in a user's mouth and spend their money. There is
    no cryptographic signature to fall back on: this is a WhatsApp Web session,
    not a signed platform webhook.
    """
    expected = os.getenv("WHATSAPP_GATEWAY_SECRET") or ""
    provided = request.headers.get("x-gateway-secret") or ""
    if not expected or not provided:
        return False
    # Constant-time: a fast reject leaks the secret byte by byte.
    return hmac.compare_digest(expected, provided)


@router.post("/webhook/inbound")
async def inbound(request: Request):
    """One message from the Baileys gateway.

        {"message_id": "3EB0…", "phone": "919876543210",
         "text": "I need milk", "timestamp": "1755000000", "type": "text"}

    Does the minimum: authenticate, validate, persist, return 200 in
    milliseconds. All reasoning happens in the background worker, because LLM
    and provider calls take far longer than the gateway's timeout allows.

    Status codes matter here — the gateway retries on 5xx and gives up on 4xx:
      200  accepted, or deliberately ignored
      401  bad or missing secret
      400  unusable payload; retrying would fail identically
    """
    if not _gateway_secret_ok(request):
        logger.warning("[webhook] rejected: bad or missing X-Gateway-Secret")
        raise HTTPException(status_code=401, detail="Unauthorized")

    try:
        payload = await request.json()
    except Exception:
        logger.warning("[webhook] rejected: body was not JSON")
        raise HTTPException(status_code=400, detail="Malformed payload")

    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="Body must be a JSON object")

    phone = whatsapp.canonical_phone(payload.get("phone"))
    message_id = str(payload.get("message_id") or "")
    if not phone or not message_id:
        # Not retryable: the same payload would fail identically forever. The
        # message id is also the deduplication key, so a message without one
        # could be processed twice.
        logger.warning("[webhook] rejecting a message with no phone or no id")
        raise HTTPException(status_code=400,
                            detail="phone and message_id are required")

    kind = str(payload.get("type") or "text")
    body = str(payload.get("text") or "")

    # A VOICE NOTE becomes text right here, at the boundary. Everything below —
    # queue, worker, concierge, planner, skills, providers — receives exactly
    # what a typed message produces and never learns speech was involved.
    spoken_language = None
    if kind == "audio":
        body, spoken_language, failure = await _transcribe(payload)
        if failure:
            # Never silence. A voice note that produced nothing gets a plain
            # text answer saying so, which is queued like any other reply.
            await _queue(message_id, phone, "", unsupported=True, note=failure)
            return {"status": "received"}
        kind = "voice"

    if not body and kind == "text":
        # The gateway already drops protocol plumbing; an empty text that gets
        # this far is nothing to answer.
        return {"status": "ignored"}

    if _debug_enabled():
        logger.info(f"[webhook] 📩 {phone}: {body!r}")
    else:
        logger.info(f"[webhook] inbound {message_id} from {phone} ({kind})")

    # UNCHANGED contract: message_sid is WhatsApp's own id, which is what the
    # queue deduplicates on. A transcribed voice note is ordinary text by now;
    # `voice` records only HOW it arrived, so the reply can go back the same way.
    await _queue(message_id, phone, body,
                 unsupported=(kind not in ("text", "voice")),
                 voice=(kind == "voice"), language=spoken_language)
    return {"status": "received"}


async def _transcribe(payload: dict):
    """Voice note -> (text, language, failure_message).

    FAILS SOFT, never closed: with no SARVAM_API_KEY, or if speech recognition
    is down, the caller sends a plain text explanation instead. A voice note
    that produces nothing at all reads as a broken bot.
    """
    import base64

    from ai import voice

    encoded = payload.get("audio")
    if not encoded:
        # The gateway could not decrypt the media. It said so in its own log.
        return "", None, VOICE_UNREADABLE

    if not voice.is_configured():
        logger.warning("[webhook] a voice note arrived but SARVAM_API_KEY is not set")
        return "", None, VOICE_UNAVAILABLE

    try:
        audio = base64.b64decode(encoded)
    except Exception:
        logger.error("[webhook] voice note audio was not valid base64")
        return "", None, VOICE_UNREADABLE

    try:
        text, language, confidence = await voice.transcribe(
            audio, str(payload.get("mimetype") or "audio/ogg")
        )
    except voice.VoiceUnavailable as e:
        logger.warning(f"[webhook] could not transcribe a voice note: {e}")
        return "", None, VOICE_UNREADABLE

    return text, language, None
