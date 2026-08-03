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
from datetime import datetime

from core.llm import get_llm
from core.logger import logger

from ai import conversation, identity, memory, skills

# A turn may chain a few tool calls (recall -> search -> answer). Bounded so a
# confused model cannot loop forever on the user's dime.
MAX_STEPS = 4

SYSTEM_PROMPT = """\
You are a food concierge who chats over WhatsApp. You help people decide what to
eat and then get it ordered.

HOW TO TALK
- Like a friend who knows food. Warm, brief, useful. Never robotic, never salesy,
  never breathlessly excited.
- Short messages. This is WhatsApp, not email. Two or three lines is usually
  plenty; a list of options can be longer.
- No corporate filler. Never open with "Certainly!", "Great question!" or "I'd be
  happy to help". Just answer.
- Ask a follow-up ONLY when you genuinely cannot proceed without it. If they say
  "I'm hungry" and you know how they eat, suggest something — don't interview
  them.
- Light emoji is fine, at most one per message. Never markdown headers, never
  bullet characters, never bold syntax.

PRESENTING OPTIONS
When find_food returns options, lay them out exactly like this — number, name,
then the facts that came back, then one short line of why it suits THEM:

  I found three good options nearby.

  1. Meghana Foods
  ⭐ 4.6 · ₹340 · 22 min
  You usually go for spicy biryani, so this is right up your street.

  2. Empire Restaurant
  ⭐ 4.3 · ₹280 · 18 min
  Cheaper and quicker, still comfortably in budget.

  Which one?

Only include a rating, price or ETA if the tool actually returned it. Omit what
you don't have — never write "N/A", never guess, never round a missing number
into existence.

TRUTHFULNESS — THIS IS ABSOLUTE
- You may only mention a restaurant, dish, price, rating or delivery time that
  came back from a tool in this conversation. Never invent one.
- If a tool reports a capability is unavailable, say so plainly and offer the
  next best thing you CAN do. Never paper over a gap with a plausible guess.
- Never claim an order was placed, cancelled or delivered unless a tool said so.
- When you recommend something, give the real reason from the tool's output.

USING WHAT YOU KNOW
Weave memory in as if you simply know them. Never announce that you remembered.

  Say:    "You usually keep it under ₹350, so this fits."
  Say:    "You had Truffles last week and liked it."
  Never:  "I remember that you..."  /  "According to my memory..."
  Never:  "As an AI..."

Use the time and day given below. Friday night and Tuesday lunch are different
meals. Never ask for something you already know.

ORDERING
- When they pick an option ("the second one", "go with Meghana"), call
  place_order with that option's NUMBER as shown.
- Before spending money, confirm once in a single line with the item and price,
  then place it when they agree. Don't make a ceremony of it.
- After ordering, give the ETA if there is one and let them know they can ask
  where it is.

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
- First ever message: one short, warm line saying who you are and what you do,
  then answer what they actually asked. Don't deliver a feature tour.
- Ask at most ONE profile question per message, and only when it's naturally
  relevant. Everything else you learn by paying attention.

FAILURES
Something will break. Handle it like a person would: say what happened in plain
words, never show error codes or internal details, and always offer the next
best thing. "That restaurant just closed — want me to look at the other two?"
is good. "Provider returned 502" is not.
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
                            "'restaurant' when they want a cooked meal delivered — "
                            "biryani, pizza, burgers, dinner, 'something to eat'. "
                            "'grocery' when they want ingredients or household items "
                            "— milk, onions, bread, eggs, 'groceries'. "
                            "If they name a dish, it is 'restaurant'. If they name a "
                            "raw ingredient, it is 'grocery'. When genuinely "
                            "ambiguous, ask in one short line."
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
            "name": "place_order",
            "description": (
                "Order one of the options most recently shown, by its number. Use "
                "when the user picks one ('the second one', 'go with Meghana', "
                "'yes' to a confirmation). Only call this once they have chosen."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "selection": {
                        "type": "integer",
                        "description": "The option's number exactly as shown to the user (1, 2, 3…).",
                    },
                    "quantity": {"type": "integer", "description": "How many. Defaults to 1."},
                },
                "required": ["selection"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "retry_order",
            "description": (
                "Retry the order that just failed, using the SAME item already "
                "chosen. Use whenever they want another attempt. Never call "
                "find_food to 'refresh' before retrying — the item is remembered."
            ),
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "abandon_order",
            "description": (
                "Drop a failed order the user no longer wants to retry. Their "
                "earlier options stay available."
            ),
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "check_order",
            "description": (
                "Check the user's current order — 'where's my order?', 'how much "
                "longer?', 'has it left yet?'."
            ),
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "cancel_order",
            "description": "Cancel the user's active order, when they ask to.",
            "parameters": {"type": "object", "properties": {}},
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

        if call.name == "place_order":
            result = await skills.place_order(
                user,
                selection=args.get("selection"),
                quantity=args.get("quantity", 1),
            )
            return result.message

        if call.name == "retry_order":
            return (await skills.retry_pending_order(user)).message

        if call.name == "abandon_order":
            return (await skills.cancel_pending_order(user)).message

        if call.name == "check_order":
            return (await skills.check_order(user)).message

        if call.name == "cancel_order":
            return (await skills.cancel_order(user)).message

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


def _now_context() -> str:
    """Time and day, so 'Friday night' and 'Tuesday lunch' mean something.

    Local time matters more than UTC here — people eat on their own clock — so
    this uses the server's local time and states it plainly rather than
    pretending to know the user's timezone.
    """
    now = datetime.now()
    meal = (
        "breakfast time" if 5 <= now.hour < 11
        else "lunchtime" if 11 <= now.hour < 16
        else "dinner time" if 16 <= now.hour < 22
        else "late night"
    )
    return f"Right now it is {now:%A}, {now:%d %B}, {now:%H:%M} — {meal}."


def _state_context(user) -> str:
    """Tell the model what is in flight — as DATA, not as something to infer
    from the transcript. Tool results vanish at the end of a turn, so without
    this the model has only prose to go on."""
    state = conversation.load(user.phone)

    if state.awaiting_retry and state.pending:
        remaining = conversation.MAX_RETRIES - state.pending.retry_count
        return (
            f"IN FLIGHT: an order for {state.pending.describe()} FAILED and is "
            f"waiting on their answer. If they agree, retry THAT order — never "
            f"search again. Retries left: {remaining}."
        )
    if state.state == conversation.State.AWAITING_SELECTION and state.has_offers:
        return (f"IN FLIGHT: {len(state.offers)} options were just shown and they "
                f"haven't chosen yet.")
    if state.state == conversation.State.ORDER_FAILED and state.pending:
        return (f"IN FLIGHT: '{state.pending.title}' could not be placed after "
                f"repeated attempts. Do not retry it again; offer alternatives.")
    return "Nothing is in flight."


def build_context(user, message):
    """Assemble the prompt: instructions, what we know, the conversation, the
    new message. Memory is injected as data — the model never has to recall it."""
    system = (
        f"{SYSTEM_PROMPT}\n"
        f"CONTEXT\n{_now_context()}\n\n"
        f"WHAT YOU KNOW ABOUT THIS USER\n{user.describe()}\n"
        f"{_connection_status(user)}\n"
        f"{_state_context(user)}"
    )
    return [{"role": "system", "content": system}, *user.history,
            {"role": "user", "content": message}]


async def _resolve_state_first(user, message: str):
    """Handle turns whose meaning is determined by conversation STATE, not by
    interpretation.

    When we have asked a closed question ("shall I retry?"), the answer is
    classified in code and the matching action executed directly. Letting the
    model choose here is what caused it to launch a fresh search instead of
    retrying — the failure this state machine exists to prevent.

    Returns a tool-result string for the model to phrase, or None to fall
    through to ordinary planning.
    """
    state = conversation.load(user.phone)

    if state.awaiting_retry:
        intent = conversation.classify_reply(message)

        if intent == conversation.AFFIRMATIVE:
            logger.info("[planner] retry confirmed — reusing the pending order")
            return (await skills.retry_pending_order(user)).message

        if intent == conversation.NEGATIVE:
            return (await skills.cancel_pending_order(user)).message

        if intent == conversation.ALTERNATIVE:
            await skills.cancel_pending_order(user)
            return None      # they want something else; let the model search

        # Not a yes/no — maybe they named a different option from the same list.
        picked = conversation.resolve_selection(message, len(state.offers))
        if picked:
            await skills.cancel_pending_order(user)
            return (await skills.place_order(user, selection=picked)).message
        return None

    # Waiting on a choice: resolve "first one" / "option 2" deterministically.
    if state.state == conversation.State.AWAITING_SELECTION and state.has_offers:
        picked = conversation.resolve_selection(message, len(state.offers))
        if picked:
            logger.info(f"[planner] selection resolved to option {picked}")
            return (await skills.place_order(user, selection=picked)).message

    return None


async def plan(phone: str, message: str) -> str:
    """Run one turn end to end and return the reply text."""
    user = identity.load(phone)                    # 1. identity + memory retrieval
    llm = get_llm()

    # State-driven turns are resolved before the model is consulted, so the
    # outcome is deterministic. The model still writes the words.
    forced = await _resolve_state_first(user, message)
    if forced is not None:
        messages = build_context(user, message)
        messages.append({"role": "user", "content": f"[system: {forced}]"})
        reply = await llm.chat(messages)
        return reply.text

    messages = build_context(user, message)

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
