"""
AI Food Concierge — conversation entry point.

The seam between transport and product. Everything above is plumbing (webhook,
queue, worker, delivery); everything below is the concierge.

Thin by design: run the planner, persist the turn, keep failures friendly.
"""
from ai import identity, memory
from ai.planner import plan
from core.logger import logger

EMPTY_MESSAGE_REPLY = "Tell me what you're in the mood for and I'll take it from there 🙂"
ERROR_REPLY = "Something went wrong on my end — mind trying that again?"


async def respond(phone: str, message: str) -> str:
    """Return the assistant's reply to one inbound user message."""
    message = (message or "").strip()
    if not message:
        return EMPTY_MESSAGE_REPLY

    try:
        reply = await plan(phone, message)
    except Exception:
        # The user must never see a stack trace or a provider's raw error.
        logger.error(f"[concierge] turn failed for {phone}", exc_info=True)
        return ERROR_REPLY

    if not reply:
        reply = EMPTY_MESSAGE_REPLY

    # Persist only completed turns, so a failed turn cannot poison future context.
    try:
        memory.record_turn(phone, message, reply)
        # Onboarding finishes by conversation, not by interrogation: once the
        # essentials have surfaced naturally, stop treating them as open.
        identity.refresh_onboarding_status(phone)
    except Exception:
        logger.error(f"[concierge] could not persist turn for {phone}", exc_info=True)

    return reply
