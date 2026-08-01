"""
AI Food Concierge — conversation entry point.

This is the seam the transport layer talks to. Everything above it (webhook,
queue, worker, delivery) is transport plumbing; everything below it is product.

PHASE 3 fills this in: async multi-turn LLM conversation, planner, memory.
Right now it is a deliberate placeholder so the app boots and the delivery
pipeline stays verifiable end to end after the Phase 2 deletion.
"""

_PLACEHOLDER = (
    "👋 Hey! I'm your food concierge — still being wired up.\n\n"
    "Soon you'll just tell me what you're in the mood for and I'll figure out "
    "the rest."
)


async def respond(phone: str, message: str) -> str:
    """Return the assistant's reply to one inbound user message."""
    return _PLACEHOLDER
