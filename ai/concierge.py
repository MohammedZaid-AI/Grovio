"""
AI Food Concierge — conversation entry point.

The seam between transport and product. Everything above is plumbing (webhook,
queue, worker, delivery); everything below is the concierge.

Thin by design: run the planner, persist the turn, keep failures friendly.

VOICE IS A BOUNDARY CONCERN, HANDLED HERE AND IN backend/routes.py.
The planner, skills, memory and the provider layer receive text and return text
whether the person typed or spoke. A voice note is transcribed before it
reaches `plan()` and the reply is voiced after it returns — neither step is
visible from inside.
"""
import db
from ai import identity, memory
from ai.planner import plan
from core.logger import logger

EMPTY_MESSAGE_REPLY = "Tell me what you're in the mood for and I'll take it from there 🙂"
ERROR_REPLY = "Something went wrong on my end — mind trying that again?"


async def respond(phone: str, message: str) -> str:
    """Return the assistant's reply to one inbound user message.

    ANYONE may talk to the concierge. Authorisation is not a conversation gate —
    it guards SPENDING, and lives at the point money is committed
    (`skills._execute_pending`). Recommending food to a stranger costs nothing;
    ordering it costs the account owner.
    """
    message = (message or "").strip()

    # Claimed once, whatever happens next: if this turn arrived as speech, the
    # answer goes back as speech. Claiming early means an error reply is spoken
    # too — someone who cannot read a screen gets no value from a written
    # apology.
    spoken_language = db.peek_voice_turn(phone)

    if not message:
        return await _finish(phone, "", EMPTY_MESSAGE_REPLY, spoken_language,
                             persist=False)

    try:
        reply = await plan(phone, message)
    except Exception:
        # The user must never see a stack trace or a provider's raw error.
        logger.error(f"[concierge] turn failed for {phone}", exc_info=True)
        return await _finish(phone, message, ERROR_REPLY, spoken_language,
                             persist=False)

    if not reply:
        reply = EMPTY_MESSAGE_REPLY

    return await _finish(phone, message, reply, spoken_language)


async def _finish(phone, message, reply, spoken_language, persist=True):
    """Speak the reply if the turn was spoken, then record it."""
    # The turn is over: the next message decides for itself whether the answer
    # is spoken. Cleared before speaking, so a synthesis failure cannot leave a
    # stale row that makes the NEXT typed reply arrive as audio.
    db.clear_voice_turn(phone)

    if spoken_language:
        await _speak(phone, reply, spoken_language)

    if persist:
        # Persist only completed turns, so a failed turn cannot poison future
        # context. The transcript is stored exactly as a typed message would
        # be — `source` records that it was spoken, which is what lets a later
        # turn know this person talks rather than types.
        try:
            memory.record_turn(phone, message, reply,
                               source="voice" if spoken_language else "text")
            # Onboarding finishes by conversation, not by interrogation: once
            # the essentials have surfaced naturally, stop treating them as open.
            identity.refresh_onboarding_status(phone)
        except Exception:
            logger.error(f"[concierge] could not persist turn for {phone}", exc_info=True)

    return reply


async def _speak(phone: str, reply: str, language: str) -> None:
    """Send the reply as a voice note, alongside the text.

    ALONGSIDE, not instead. The text still goes out through the worker's normal
    outbound queue, so it inherits ordering, retries and restart recovery, and
    a numbered list of options stays readable and scrollable — which speech is
    not. The voice note is the part that serves someone who does not read
    comfortably.

    Entirely best effort. Speech failing must never cost someone their reply.
    """
    from ai import voice

    try:
        audio = await voice.say(reply, language)
    except voice.VoiceUnavailable as e:
        logger.warning(f"[concierge] could not voice a reply for {phone}: {e}")
        return
    except Exception:
        logger.error(f"[concierge] voice synthesis crashed for {phone}", exc_info=True)
        return

    # The transport seam, same as every other outbound message. The concierge
    # does not know what carries it.
    from whatsapp import send_audio

    try:
        await send_audio(phone, audio)
    except Exception:
        logger.error(f"[concierge] could not send a voice note to {phone}", exc_info=True)
