"""
Conversation state machine — the fix for the infinite recommendation loop.

The bug: a failed order left no structured record, so "yes" was interpreted from
chat prose and the model started a fresh search instead of retrying. These tests
pin the behaviour that makes that impossible.

The LLM is scripted throughout, so what is asserted is the STATE MACHINE, not
model judgement.
"""
import asyncio
import os
import sys
import tempfile

sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from cryptography.fernet import Fernet

os.environ["TOKEN_ENCRYPTION_KEY"] = Fernet.generate_key().decode()

import db

_tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
_tmp.close()
db.DB_PATH = _tmp.name
db.init_db()

from ai import concierge, conversation, identity, planner, skills
from ai.providers import ProviderKind, registry
from ai.providers.base import ItemUnavailable, Offer, PlacedOrder, ProviderError, PLACED
from ai.conversation import State
from core.llm import LLMReply

_passed = _failed = 0


def check(name, condition):
    global _passed, _failed
    if condition:
        _passed += 1
        print(f"  ✅ {name}")
    else:
        _failed += 1
        print(f"  ❌ {name}")


def run(coro):
    return asyncio.run(coro)


class Kitchen:
    """Ordering provider whose failures are scriptable."""
    name = "sm_kitchen"
    display_name = "Kitchen"
    kind = ProviderKind.RESTAURANT
    supports_tracking = False
    supports_cancellation = False

    def __init__(self):
        self.offers = [
            Offer(provider=self.name, kind=self.kind, id="sku-A", title="Chicken Biryani",
                  venue="Meghana", price=340, rating=4.6, eta_minutes=22),
            Offer(provider=self.name, kind=self.kind, id="sku-B", title="Mutton Biryani",
                  venue="Empire", price=280, rating=4.3, eta_minutes=18),
            Offer(provider=self.name, kind=self.kind, id="sku-C", title="Veg Biryani",
                  venue="Nagarjuna", price=210, rating=4.4, eta_minutes=26),
        ]
        self.fail_with = None
        self.placed = []
        self.searches = 0

    async def search(self, query, ctx):
        self.searches += 1
        return self.offers

    async def place(self, offer, quantity, ctx):
        if self.fail_with:
            raise self.fail_with
        self.placed.append(offer.id)
        return PlacedOrder(provider=self.name, order_id=f"ORD-{len(self.placed)}",
                           status=PLACED, eta_minutes=offer.eta_minutes,
                           total=offer.price, items=(offer.title,))


class Script:
    def __init__(self, *replies):
        self.replies = list(replies)
        self.calls = []

    async def chat(self, messages, tools=None, temperature=None):
        self.calls.append(list(messages))
        return self.replies.pop(0) if self.replies else LLMReply(text="ok")


def say(phone, text, *replies):
    """One user turn, with the model's words scripted."""
    planner.get_llm = lambda: Script(*(replies or (LLMReply(text="ok"),)))
    return run(concierge.respond(phone, text))


kitchen = Kitchen()
registry.clear()
registry.register(kitchen)

PHONE = "919922200001"


def fresh(phone=PHONE):
    """Reset to a clean conversation and show the three options."""
    conversation.reset(phone)
    kitchen.fail_with = None
    kitchen.placed.clear()
    kitchen.searches = 0
    run(skills.find_food(identity.load(phone), "biryani", "restaurant"))


# ----------------------------------------------------------------------
print("\n[1] The state machine itself")
conversation.reset(PHONE)
check("starts IDLE", conversation.load(PHONE).state == State.IDLE)

run(skills.find_food(identity.load(PHONE), "biryani", "restaurant"))
state = conversation.load(PHONE)
check("showing options -> AWAITING_SELECTION", state.state == State.AWAITING_SELECTION)
check("the offer list is persisted", len(state.offers) == 3)
check("no pending order yet", state.pending is None)

kitchen.fail_with = ProviderError("upstream 500")
run(skills.place_order(identity.load(PHONE), selection=1))
state = conversation.load(PHONE)
check("provider failure -> AWAITING_RETRY_CONFIRMATION",
      state.state == State.AWAITING_RETRY_CONFIRMATION)
check("PENDING ORDER PERSISTED across the failure", state.pending is not None)
check("pending records the chosen offer", state.pending.offer_id == "sku-A")
check("pending records the selection number", state.pending.selection == 1)
check("pending records provider", state.pending.provider == kitchen.name)
check("pending records price for confirmation", state.pending.price == 340)
check("provider error kept for logs", "500" in (state.pending.last_error or ""))
check("retry count starts at zero", state.pending.retry_count == 0)

try:
    conversation._save(conversation.load(PHONE), State.ORDER_COMPLETE)
    check("illegal transition rejected", False)
except conversation.IllegalTransition:
    check("illegal transition rejected", True)

# ----------------------------------------------------------------------
print("\n[2] recommend -> select -> failure -> 'yes' RETRIES THE SAME ORDER")
fresh()
kitchen.fail_with = ProviderError("boom")
say(PHONE, "1")
searches_before = kitchen.searches
check("failed once", conversation.load(PHONE).awaiting_retry)

kitchen.fail_with = None
say(PHONE, "Yes")

check("retry placed an order", kitchen.placed == ["sku-A"])
check("SAME offer retried, not a new choice", kitchen.placed[-1] == "sku-A")
check("NO new search was performed", kitchen.searches == searches_before)
check("state is ORDER_COMPLETE", conversation.load(PHONE).state == State.ORDER_COMPLETE)
check("pending cleared after success", conversation.load(PHONE).pending is None)

# ----------------------------------------------------------------------
print("\n[3] recommend -> select -> failure -> cancel")
fresh()
kitchen.fail_with = ProviderError("boom")
say(PHONE, "1")
say(PHONE, "no thanks")

state = conversation.load(PHONE)
check("cancelling drops the pending order", state.pending is None)
check("offers survive so they can pick another", len(state.offers) == 3)
check("back to AWAITING_SELECTION", state.state == State.AWAITING_SELECTION)
check("nothing was ordered", kitchen.placed == [])

# ----------------------------------------------------------------------
print("\n[4] recommend -> select -> failure -> 'show me something else'")
fresh()
kitchen.fail_with = ProviderError("boom")
say(PHONE, "1")
searches_before = kitchen.searches
say(PHONE, "show me something else")

check("pending dropped", conversation.load(PHONE).pending is None)
check("falls through to the model so it can search again",
      conversation.classify_reply("show me something else") == conversation.ALTERNATIVE)

# ----------------------------------------------------------------------
print("\n[5] recommend -> select -> success (no regression)")
fresh()
say(PHONE, "2")
check("the SECOND option was ordered", kitchen.placed == ["sku-B"])
check("state ORDER_COMPLETE", conversation.load(PHONE).state == State.ORDER_COMPLETE)
check("offers spent after ordering", not conversation.load(PHONE).has_offers)

# ----------------------------------------------------------------------
print("\n[6] recommend -> 'first one' -> failure -> retry -> success")
fresh()
kitchen.fail_with = ProviderError("boom")
say(PHONE, "First one")
check("'First one' resolved to option 1", conversation.load(PHONE).pending.offer_id == "sku-A")

kitchen.fail_with = None
say(PHONE, "go ahead")
check("'go ahead' retried the same order", kitchen.placed == ["sku-A"])

# ----------------------------------------------------------------------
print("\n[7] recommend -> 'option 2' -> failure -> retry -> success")
fresh()
kitchen.fail_with = ProviderError("boom")
say(PHONE, "option 2")
check("'option 2' resolved to option 2", conversation.load(PHONE).pending.offer_id == "sku-B")

kitchen.fail_with = None
say(PHONE, "please do")
check("'please do' retried the same order", kitchen.placed == ["sku-B"])
check("still only one order placed", len(kitchen.placed) == 1)

# ----------------------------------------------------------------------
print("\n[8] Retry limit — terminates instead of looping forever")
fresh()
kitchen.fail_with = ProviderError("always down")
say(PHONE, "1")
for attempt in range(conversation.MAX_RETRIES + 3):
    say(PHONE, "yes")

state = conversation.load(PHONE)
check("ends in ORDER_FAILED, not an endless loop", state.state == State.ORDER_FAILED)
check("retries capped at MAX_RETRIES",
      state.pending.retry_count <= conversation.MAX_RETRIES)
check("never searched again while retrying", kitchen.searches == 1)
check("nothing was ordered", kitchen.placed == [])

result = run(skills.retry_pending_order(identity.load(PHONE)))
check("further retries refused", "RETRY LIMIT REACHED" in result.message)
check("alternatives offered by name", "Mutton Biryani" in result.message)
check("told to make clear nothing was charged", "nothing was charged" in result.message.lower())

# ----------------------------------------------------------------------
print("\n[9] Natural language resolution")
for phrase, expected in [
    ("1", 1), ("one", 1), ("First", 1), ("first one", 1), ("Option 1", 1),
    ("number one", 1), ("go with the first", 1), ("let's do the first one", 1),
    ("the top one", 1), ("2", 2), ("two", 2), ("second", 2), ("option 2", 2),
    ("the second one", 2), ("3", 3), ("third", 3), ("the last one", 3),
]:
    got = conversation.resolve_selection(phrase, 3)
    check(f"{phrase!r} -> {expected}", got == expected)

check("out of range rejected", conversation.resolve_selection("7", 3) is None)
check("quantity in a sentence is NOT a selection",
      conversation.resolve_selection("can I get 2 portions of that biryani please", 3) is None)
check("gibberish rejected", conversation.resolve_selection("hmm maybe later", 3) is None)

print("\n   affirmatives")
for phrase in ["yes", "Yes", "yeah", "yep", "sure", "ok", "okay", "go ahead",
               "please do", "retry", "try again", "continue", "do it", "haan"]:
    check(f"{phrase!r} -> affirmative",
          conversation.classify_reply(phrase) == conversation.AFFIRMATIVE)

print("\n   negatives and alternatives")
for phrase in ["no", "nope", "cancel", "stop", "never mind", "forget it"]:
    check(f"{phrase!r} -> negative",
          conversation.classify_reply(phrase) == conversation.NEGATIVE)
for phrase in ["something else", "show me other options", "another one", "different"]:
    check(f"{phrase!r} -> alternative",
          conversation.classify_reply(phrase) == conversation.ALTERNATIVE)
check("'no, show me something else' prefers ALTERNATIVE",
      conversation.classify_reply("no, show me something else") == conversation.ALTERNATIVE)
check("ambiguous stays UNCLEAR",
      conversation.classify_reply("what about pizza") == conversation.UNCLEAR)

# ----------------------------------------------------------------------
print("\n[10] Expiry — a stale 'yes' cannot resurrect an old order")
fresh()
kitchen.fail_with = ProviderError("boom")
say(PHONE, "1")
check("pending exists", conversation.load(PHONE).pending is not None)

db.get_connection().execute(
    "UPDATE conversation_state SET updated_at = '2020-01-01 00:00:00' WHERE phone = ?",
    (PHONE,),
).connection.commit()
stale = conversation.load(PHONE)
check("expired conversation reads as IDLE", stale.state == State.IDLE)
check("expired offers are not selectable", not stale.has_offers)
check("expired conversation is not awaiting retry", not stale.awaiting_retry)

kitchen.fail_with = None
result = run(skills.retry_pending_order(identity.load(PHONE)))
check("retrying an expired order is refused", result.status == skills.SkillStatus.STALE)
check("nothing ordered from a stale yes", kitchen.placed == [])

# ----------------------------------------------------------------------
print("\n[11] Long-term memory and conversation state stay separate")
from ai import memory

memory.remember_fact(PHONE, "budget", "500")
fresh()
kitchen.fail_with = ProviderError("boom")
say(PHONE, "1")

check("conversation state holds the pending order",
      conversation.load(PHONE).pending is not None)
check("long-term memory holds preferences",
      memory.load(PHONE).facts.get("budget") == "500")
conversation.reset(PHONE)
check("resetting the conversation does NOT erase preferences",
      memory.load(PHONE).facts.get("budget") == "500")
check("resetting the conversation clears the pending order",
      conversation.load(PHONE).pending is None)

import pathlib
root = pathlib.Path(__file__).resolve().parent.parent
memory_src = (root / "ai" / "memory.py").read_text(encoding="utf-8")
check("ai/memory.py knows nothing about pending orders",
      "pending" not in memory_src.lower() and "retry" not in memory_src.lower())

print("\n" + "=" * 70)
print(f"RESULT: {_passed} passed, {_failed} failed")
try:
    os.unlink(_tmp.name)
except OSError:
    pass
sys.exit(1 if _failed else 0)
