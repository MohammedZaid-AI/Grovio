"""
Phase 5: the complete user journey.

  "I'm hungry" -> welcome -> connect -> resume -> recommend -> "the second one"
  -> order -> ETA -> "where's my order?"

The LLM is scripted so the ASSERTIONS are about the product, not model quality:
what the user can reach, what the system refuses to invent, and how it behaves
when things break.
"""
import asyncio
import json
import os
import sys
import tempfile
from unittest.mock import AsyncMock, patch

sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from cryptography.fernet import Fernet

os.environ["TOKEN_ENCRYPTION_KEY"] = Fernet.generate_key().decode()
os.environ["PUBLIC_BASE_URL"] = "https://concierge.example"
os.environ["WHATSAPP_TRANSPORT"] = "twilio"

import db

_tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
_tmp.close()
db.DB_PATH = _tmp.name
db.init_db()

from ai import concierge, conversation, identity, memory, planner, skills
from ai.providers import ProviderKind, oauth, registry
from ai.providers.base import (
    CANCELLED, ON_THE_WAY, PLACED, ItemUnavailable, Offer, OrderStatus,
    PlacedOrder, ProviderError,
)
from core.llm import LLMReply, ToolCall

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


METADATA = {
    "authorization_endpoint": "https://p.example/authorize",
    "token_endpoint": "https://p.example/token",
}

DINER = "919911100001"


class Kitchen:
    """A restaurant provider that can be told how to misbehave."""

    name = "test_kitchen"
    display_name = "Test Kitchen"
    kind = ProviderKind.RESTAURANT
    supports_tracking = True
    supports_cancellation = True
    oauth = oauth.OAuthConfig(server_url="https://kitchen.example/mcp")

    def __init__(self):
        self.offers = [
            Offer(provider=self.name, kind=self.kind, id="sku-meghana",
                  title="Chicken Biryani", venue="Meghana Foods", price=340,
                  rating=4.6, eta_minutes=22),
            Offer(provider=self.name, kind=self.kind, id="sku-empire",
                  title="Mutton Biryani", venue="Empire Restaurant", price=280,
                  rating=4.3, eta_minutes=18),
        ]
        self.fail_with = None
        self.placed = []
        self.status = OrderStatus(order_id="ORD-1", status=ON_THE_WAY, eta_minutes=9)
        self.cancel_result = True

    async def search(self, query, ctx):
        return self.offers

    async def place(self, offer, quantity, ctx):
        if self.fail_with:
            raise self.fail_with
        self.placed.append((offer.id, quantity))
        return PlacedOrder(provider=self.name, order_id="ORD-1", status=PLACED,
                           eta_minutes=offer.eta_minutes, total=offer.price,
                           items=(offer.title,))

    async def track(self, order_id, ctx):
        return self.status

    async def cancel(self, order_id, ctx):
        return self.cancel_result


class Script:
    """Plays scripted LLM replies and records the prompts it was given."""

    def __init__(self, *replies):
        self.replies = list(replies)
        self.calls = []

    async def chat(self, messages, tools=None, temperature=None):
        self.calls.append(list(messages))
        return self.replies.pop(0) if self.replies else LLMReply(text="ok")

    @property
    def system(self):
        return self.calls[0][0]["content"]


def use(script):
    planner.get_llm = lambda: script
    return script


def link(phone, provider_name):
    from core import crypto
    db.save_provider_link(phone, provider_name, crypto.encrypt("tok"), None, None)


kitchen = Kitchen()
registry.clear()
registry.register(kitchen)

# ----------------------------------------------------------------------
print("\n[1] First contact — a stranger says 'I'm hungry'")
script = use(Script(
    LLMReply(tool_calls=[ToolCall("c1", "find_food", {"query": "dinner", "kind": "restaurant"})]),
    LLMReply(text="Hey! I'm your food concierge 👋 Connect Swiggy and I'll sort dinner."),
))
with patch.object(oauth, "discover", new=AsyncMock(return_value=METADATA)):
    reply = run(concierge.respond(DINER, "I'm hungry"))

check("user is created on first contact", identity.load(DINER).phone == DINER)
check("starts un-onboarded", identity.load(DINER).onboarding_status == identity.NEW)
check("a reply comes back", bool(reply))
tool_msg = next(m["content"] for m in script.calls[1] if m.get("role") == "tool")
check("unlinked user is asked to connect, not fobbed off", tool_msg.startswith("NEEDS_LINK"))
check("a real link URL is offered", "https://p.example/authorize" in tool_msg)
check("model told not to invent food meanwhile", "do not invent" in tool_msg.lower())
check("their original request was stored for resumption",
      db.get_connection().execute(
          "SELECT pending_message FROM oauth_states ORDER BY rowid DESC LIMIT 1"
      ).fetchone()[0] == "I'm hungry")

print("\n[2] Prompt carries the context the personality needs")
check("time of day supplied", any(w in script.system for w in
      ("breakfast time", "lunchtime", "dinner time", "late night")))
check("day of week supplied", any(d in script.system for d in
      ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")))
check("told never to announce remembering", "never announce that you remembered" in script.system.lower()
      or "never:" in script.system.lower())
check("given the exact option layout", "⭐" in script.system and "Which one?" in script.system)
check("told to omit unknown fields, not fake them", "never write \"n/a\"" in script.system.lower())
check("forbidden from claiming an order it didn't place",
      "never claim an order was placed" in script.system.lower())

# ----------------------------------------------------------------------
print("\n[3] After linking, the conversation resumes itself")
state = db.get_connection().execute(
    "SELECT state FROM oauth_states ORDER BY rowid DESC LIMIT 1").fetchone()[0]
with patch.object(oauth, "discover", new=AsyncMock(return_value=METADATA)), \
     patch.object(oauth, "_post_token", new=AsyncMock(
         return_value={"access_token": "live", "token_type": "Bearer"})):
    completed = run(oauth.complete(state, "code", registry.oauth_config_for))
identity.mark_linked(DINER)

check("linking returns the original request", completed["pending_message"] == "I'm hungry")
check("account now shows as connected", identity.load(DINER).has_linked(kitchen.name))

# ----------------------------------------------------------------------
print("\n[4] Recommendations come from real provider data")
memory.remember_fact(DINER, "budget", "400")
memory.remember_food(DINER, "Chicken Biryani", memory.LIKED)

result = run(skills.find_food(identity.load(DINER), "biryani", "restaurant"))
check("real options returned", result.status == skills.SkillStatus.OK)
check("numbered for the user", "1." in result.message and "2." in result.message)
check("real venue named", "Meghana Foods" in result.message)
check("real rating carried through", "4.6" in result.message)
check("real ETA carried through", "22 min" in result.message)
check("favourite ranked first", result.offers[0].offer.title == "Chicken Biryani")
check("reason is derived, not invented", any("regularly" in r for r in result.offers[0].reasons))
check("model told to use ONLY these", "ONLY these" in result.message)

stored = conversation.load(DINER).offers
check("what was shown is persisted for selection", [o["title"] for o in stored] ==
      [r.offer.title for r in result.offers])

# ----------------------------------------------------------------------
print("\n[5] 'Order the second one'")
second = stored[1]["title"]
order = run(skills.place_order(identity.load(DINER), selection=2))
check("order placed", order.status == skills.SkillStatus.OK)
check("the SECOND option was ordered, not the first", kitchen.placed[-1][0] == "sku-empire")
check("confirmation states the real total", "280" in order.message)
check("confirmation states the real ETA", "18" in order.message)
check("model told not to invent a delivery time", "do not invent" in order.message.lower())

saved = db.get_latest_order(DINER)
check("order persisted", saved["title"] == second and saved["provider_order_id"] == "ORD-1")
check("ordering is recorded in food memory",
      any(e["item"] == second and e["sentiment"] == memory.ORDERED for e in memory.load(DINER).food))
check("offers cleared so a stale 'second one' can't re-order", not conversation.load(DINER).has_offers)

print("\n[6] The model cannot order something it never offered")
run(skills.find_food(identity.load(DINER), "biryani", "restaurant"))
bad = run(skills.place_order(identity.load(DINER), selection=99))
check("out-of-range selection refused", bad.status == skills.SkillStatus.ERROR)
check("refusal names the real range", "1–2" in bad.message or "1-2" in bad.message)
check("nothing was ordered", len(kitchen.placed) == 1)

nonsense = run(skills.place_order(identity.load(DINER), selection="the tasty one"))
check("non-numeric selection refused", nonsense.status == skills.SkillStatus.ERROR)

conversation.reset(DINER)
stale = run(skills.place_order(identity.load(DINER), selection=1))
check("ordering with nothing on the table refuses", stale.status == skills.SkillStatus.STALE)
check("told to search again rather than guess", "find_food" in stale.message)

# ----------------------------------------------------------------------
print("\n[7] 'Where's my order?'")
status = run(skills.check_order(identity.load(DINER)))
check("live status retrieved", status.status == skills.SkillStatus.OK)
check("status phrased in human terms", "out for delivery" in status.message)
check("live ETA included", "9 minutes" in status.message)
check("stored order updated from provider truth", db.get_latest_order(DINER)["status"] == ON_THE_WAY)

print("\n[8] Cancelling")
cancelled = run(skills.cancel_order(identity.load(DINER)))
check("cancellation works", cancelled.status == skills.SkillStatus.OK)
check("order marked cancelled", db.get_latest_order(DINER)["status"] == CANCELLED)
check("no active order remains", db.get_active_order(DINER) is None)

kitchen.cancel_result = False
run(skills.find_food(identity.load(DINER), "biryani", "restaurant"))
run(skills.place_order(identity.load(DINER), selection=1))
refused = run(skills.cancel_order(identity.load(DINER)))
check("provider refusing cancellation is reported honestly", refused.status == skills.SkillStatus.ERROR)
check("never claims a failed cancellation succeeded", "could not be cancelled" in refused.message)
check("order NOT marked cancelled", db.get_latest_order(DINER)["status"] != CANCELLED)

# ----------------------------------------------------------------------
print("\n[9] Graceful failures — honest, never technical")
kitchen.fail_with = ItemUnavailable("sold out")
run(skills.find_food(identity.load(DINER), "biryani", "restaurant"))
sold_out = run(skills.place_order(identity.load(DINER), selection=1))
check("sold-out item handled distinctly", sold_out.status == skills.SkillStatus.UNAVAILABLE_ITEM)
check("offers the other options instead", "other options" in sold_out.message)

kitchen.fail_with = ProviderError("500 upstream boom")
run(skills.find_food(identity.load(DINER), "biryani", "restaurant"))
broke = run(skills.place_order(identity.load(DINER), selection=1))
check("provider failure handled", broke.status == skills.SkillStatus.ERROR)
check("no internal detail leaks to the model", "500" not in broke.message and "boom" not in broke.message)
check("explicitly says nothing was charged", "nothing was charged" in broke.message.lower())
check("explicitly forbids claiming success", "not claim it succeeded" in broke.message.lower())
kitchen.fail_with = None

no_track = Kitchen()
no_track.name = "no_track_kitchen"
no_track.supports_tracking = False
no_track.supports_cancellation = False
registry.clear()
registry.register(no_track)
link(DINER, no_track.name)
run(skills.find_food(identity.load(DINER), "biryani", "restaurant"))
run(skills.place_order(identity.load(DINER), selection=1))
untracked = run(skills.check_order(identity.load(DINER)))
check("provider without tracking says so honestly",
      untracked.status == skills.SkillStatus.CAPABILITY_UNAVAILABLE)
check("still shares what IS known", "Chicken Biryani" in untracked.message)
check("forbidden from inventing a countdown", "do not" in untracked.message.lower()
      and "invent" in untracked.message.lower())

uncancellable = run(skills.cancel_order(identity.load(DINER)))
check("provider without cancellation says so honestly",
      uncancellable.status == skills.SkillStatus.CAPABILITY_UNAVAILABLE)
check("never claims a cancellation it cannot do", "not claim it was cancelled" in uncancellable.message.lower())

# Expired credentials mid-journey -> reconnect, not a crash.
registry.clear()
registry.register(kitchen)
db.revoke_provider_link(DINER, kitchen.name)
run(skills.find_food(identity.load(DINER), "biryani", "restaurant"))
conversation.show_offers(DINER, [{
    "provider": kitchen.name, "id": "sku-meghana", "title": "Chicken Biryani",
    "venue": "Meghana Foods", "price": 340, "currency": "INR",
    "eta_minutes": 22, "kind": "restaurant"}], "biryani")
with patch.object(oauth, "discover", new=AsyncMock(return_value=METADATA)):
    relink = run(skills.place_order(identity.load(DINER), selection=1))
check("revoked account mid-order asks to reconnect", relink.status == skills.SkillStatus.NEEDS_LINK)
check("reconnect offers a fresh link", relink.link_url and relink.link_url.startswith("https://"))

# ----------------------------------------------------------------------
print("\n[10] No orders yet / unknown states")
FRESH = "919911100002"
check("no order to report", run(skills.check_order(identity.load(FRESH))).status == skills.SkillStatus.EMPTY)
check("no order to cancel", run(skills.cancel_order(identity.load(FRESH))).status == skills.SkillStatus.EMPTY)

# ----------------------------------------------------------------------
print("\n[11] Full loop through the planner, end to end")
link(DINER, kitchen.name)
conversation.reset(DINER)
script = use(Script(
    LLMReply(tool_calls=[ToolCall("f1", "find_food", {"query": "biryani", "kind": "restaurant"})]),
    LLMReply(text="1. Meghana Foods\n⭐ 4.6 · ₹340 · 22 min\nYour usual pick.\n\n2. Empire\n⭐ 4.3 · ₹280 · 18 min\n\nWhich one?"),
))
shown = run(concierge.respond(DINER, "I'm craving biryani"))
check("options reach the user", "Meghana" in shown and "2." in shown)

script = use(Script(
    LLMReply(text="Done — Mutton Biryani from Empire, ₹280, about 18 minutes 🎉"),
))
placed = run(concierge.respond(DINER, "order the second one"))
check("ordering works through the planner", "Empire" in placed)
check("selection resolved WITHOUT a tool call (deterministic)", len(script.calls) == 1)
check("the right item was ordered", kitchen.placed[-1][0] == "sku-empire")

script = use(Script(
    LLMReply(tool_calls=[ToolCall("t1", "check_order", {})]),
    LLMReply(text="It's out for delivery — about 9 minutes away."),
))
tracked = run(concierge.respond(DINER, "where's my order?"))
check("tracking works through the planner", "9 minutes" in tracked)
check("whole journey persisted as conversation",
      len(memory.load(DINER).history) >= 6)

print("\n" + "=" * 70)
print(f"RESULT: {_passed} passed, {_failed} failed")
try:
    os.unlink(_tmp.name)
except OSError:
    pass
sys.exit(1 if _failed else 0)
