"""
Planner — orchestration.

Separates the four concerns the concierge needs and lets the LLM *drive* them
rather than decide everything itself:

  1. intent detection    — which capability this turn needs. The model chooses a
                           tool (or none); that choice IS the intent.
  2. memory retrieval    — ai.memory assembles the user model (never the LLM's
                           recollection, which can drift).
  3. recommendation      — ai.recommendation scores real offers and produces the
                           reasons. The LLM phrases them; it does not invent them.
  4. provider execution  — ai.providers.registry, routed by capability, never by
                           platform name.

The model can only affect the world through the tools below. It cannot reach a
provider, a database or a platform directly, which is what keeps the layering
real instead of decorative.
"""
import json

from core.llm import get_llm
from core.logger import logger

from ai import identity, memory, skills

# A turn may chain a few tool calls (recall -> search -> answer). Bounded so a
# confused model cannot loop forever on the user's dime.
MAX_STEPS = 4

SYSTEM_PROMPT = """\
You are a food concierge who chats over WhatsApp. You help people decide what to
eat and then get it ordered.

HOW TO TALK
- Like a friend who knows food, not a form. No menus of numbered options, no
  command syntax, no corporate tone.
- Keep it short. This is WhatsApp, not email.
- Ask a follow-up ONLY when you genuinely cannot proceed without it (budget,
  headcount, or location). Never interrogate. If they said "I'm hungry" and you
  know their usual, suggest it.
- Light emoji use is fine. Never use markdown headers or bullet characters.

TRUTHFULNESS — THIS IS ABSOLUTE
- You may only mention a restaurant, dish, price, rating or delivery time that
  came back from the find_food tool in this conversation. Never invent one.
- If a tool reports a capability is unavailable, say so plainly and offer what
  you CAN do. Never paper over it with a plausible-sounding suggestion.
- When you recommend something, give the real reason from the tool's output.

MEMORY
- Save durable preferences with remember (allergies, budget, home/office area,
  cuisines they love, dietary rules, gym days). Do not save one-off moods.
- Record what they order or reject with remember_food.
- Never ask for something you already know.
- When they correct you ("actually I hate mushrooms"), save it immediately.

CONNECTING ACCOUNTS
- To order anything, the user connects their own delivery account once. If a
  tool says NEEDS_LINK, share the link exactly as given, on its own line, and
  reassure them you'll continue right where they left off afterwards.
- Never ask for a password, OTP or card details. You never see their credentials
  and must never request them — the link handles everything.
- Don't nag. Mention connecting when it's needed, then let it go.

ONBOARDING
- If this is a brand new user, introduce yourself in one short line before
  anything else.
- Ask at most ONE profile question per message, and only when it's naturally
  relevant. Everything else you learn by paying attention.
"""

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "find_food",
            "description": (
                "Search for real, orderable food or groceries and get them ranked "
                "for this specific user. This is the ONLY source of restaurants, "
                "dishes, prices and delivery times. Call it before recommending "
                "anything."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "What to look for, e.g. 'biryani' or 'milk'.",
                    },
                    "kind": {
                        "type": "string",
                        "enum": ["restaurant", "grocery"],
                        "description": (
                            "'restaurant' for prepared meals/takeaway, 'grocery' for "
                            "ingredients and household items."
                        ),
                    },
                    "max_price": {
                        "type": "number",
                        "description": "Optional budget ceiling for one item.",
                    },
                },
                "required": ["query", "kind"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "connect_account",
            "description": (
                "Start connecting the user's account with a delivery platform, or "
                "reconnect one that stopped working. Use when they ask to connect, "
                "or when they want to order and no account is connected yet."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "provider": {
                        "type": "string",
                        "description": "Platform name if they named one; omit otherwise.",
                    }
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "disconnect_account",
            "description": "Disconnect a platform the user has connected, on their request.",
            "parameters": {
                "type": "object",
                "properties": {"provider": {"type": "string"}},
                "required": ["provider"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "remember",
            "description": (
                "Persist a long-term preference about this user. Use for durable "
                "facts only (allergies, budget, home area, cuisines, dietary rules). "
                "Pass an empty value to forget a fact."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "key": {
                        "type": "string",
                        "description": "Short snake_case name, e.g. 'allergies', 'budget'.",
                    },
                    "value": {"type": "string", "description": "The value to store."},
                },
                "required": ["key", "value"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "remember_food",
            "description": "Record that the user ordered, liked, disliked or rejected a dish.",
            "parameters": {
                "type": "object",
                "properties": {
                    "item": {"type": "string"},
                    "sentiment": {
                        "type": "string",
                        "enum": ["ORDERED", "LIKED", "DISLIKED", "REJECTED"],
                    },
                    "venue": {"type": "string", "description": "Where it came from, if known."},
                },
                "required": ["item", "sentiment"],
            },
        },
    },
]


# ----------------------------------------------------------------------
# Tool dispatch — the only way the model touches anything real
# ----------------------------------------------------------------------
# Every capability goes through ai/skills.py. This module deliberately has no
# idea that authorisation, tokens or OAuth exist: a skill either returns usable
# information or an instruction about why it couldn't.
async def _dispatch(call, user, message):
    """Route one tool call. Never raises — a failure is reported back to the
    model as a tool result so it can recover in-conversation."""
    args = call.arguments
    try:
        if call.name == "find_food":
            if not args.get("query"):
                return "ERROR: query is required."
            result = await skills.find_food(
                user,
                query=args["query"],
                kind=args.get("kind", "restaurant"),
                max_price=args.get("max_price"),
                pending_message=message,
            )
            return result.message

        if call.name == "connect_account":
            result = await skills.connect_provider(
                user, args.get("provider"), pending_message=message
            )
            return result.message

        if call.name == "disconnect_account":
            return skills.disconnect_provider(user, args.get("provider", "")).message

        if call.name == "remember":
            return memory.remember_fact(user.phone, args.get("key", ""), args.get("value", ""))

        if call.name == "remember_food":
            return memory.remember_food(
                user.phone,
                item=args.get("item", ""),
                sentiment=args.get("sentiment", ""),
                venue=args.get("venue"),
            )

        return f"ERROR: unknown tool {call.name!r}."
    except Exception as e:
        logger.error(f"[planner] tool {call.name} failed: {e!r}", exc_info=True)
        return f"ERROR: {call.name} failed. Continue without it."


def _assistant_turn(reply):
    """Re-encode an assistant tool-call turn for the next request."""
    return {
        "role": "assistant",
        "content": reply.text or None,
        "tool_calls": [
            {
                "id": call.id,
                "type": "function",
                "function": {"name": call.name, "arguments": json.dumps(call.arguments)},
            }
            for call in reply.tool_calls
        ],
    }


def _connection_status(user) -> str:
    """What the model may say about connected accounts — status only, never a
    credential."""
    if user.linked_providers:
        labels = ", ".join(skills._label(name) for name in user.linked_providers)
        return f"Connected accounts: {labels}."
    if user.onboarding_status == identity.NEW:
        return "This user is brand new and has connected no accounts yet."
    return "No accounts are currently connected."


def build_context(user, message):
    """Assemble the prompt: instructions, what we know, the conversation, the
    new message. Memory is injected as data — the model never has to recall it."""
    system = (
        f"{SYSTEM_PROMPT}\n"
        f"WHAT YOU KNOW ABOUT THIS USER\n{user.describe()}\n"
        f"{_connection_status(user)}"
    )
    return [{"role": "system", "content": system}, *user.history,
            {"role": "user", "content": message}]


async def plan(phone: str, message: str) -> str:
    """Run one turn end to end and return the reply text."""
    user = identity.load(phone)                    # 1. identity + memory retrieval
    messages = build_context(user, message)
    llm = get_llm()

    for _ in range(MAX_STEPS):
        reply = await llm.chat(messages, tools=TOOLS)   # 2. intent detection

        if not reply.wants_tools:
            return reply.text

        messages.append(_assistant_turn(reply))
        for call in reply.tool_calls:                   # 3./4. execution via skills
            result = await _dispatch(call, user, message)
            messages.append(
                {"role": "tool", "tool_call_id": call.id, "content": result}
            )

    # Out of steps: ask once more without tools so the user still gets an answer.
    logger.warning(f"[planner] hit MAX_STEPS for {phone}")
    final = await llm.chat(messages)
    return final.text
