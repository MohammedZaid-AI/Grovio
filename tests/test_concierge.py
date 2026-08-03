"""
Concierge stack: user model, provider abstraction, recommendation ranking, and
planner orchestration.

The LLM and every network call are mocked — this asserts the architecture and
the anti-hallucination guarantees, not model quality.
"""
import asyncio
import os
import pathlib
import sys
import tempfile

sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import db

_tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
_tmp.close()
db.DB_PATH = _tmp.name
db.init_db()

from ai import concierge, identity, memory, planner, recommendation, skills
from ai.providers import ProviderKind, SearchContext, registry
from ai.providers.base import Offer
from core.llm import LLMReply, ToolCall, _parse_arguments

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


class FakeProvider:
    """A stand-in platform. Its existence in a test — and absence of any real
    platform import — is the point."""

    def __init__(self, name, kind, offers, fail=False):
        self.name = name
        self.kind = kind
        self._offers = offers
        self._fail = fail
        self.queries = []

    async def search(self, query, ctx):
        self.queries.append(query)
        if self._fail:
            raise RuntimeError("provider exploded")
        return self._offers[: ctx.limit]


def offer(title, **kw):
    kw.setdefault("provider", "fake")
    kw.setdefault("kind", ProviderKind.RESTAURANT)
    kw.setdefault("id", title)
    return Offer(title=title, **kw)


PHONE = "919876500000"

# ----------------------------------------------------------------------
print("\n[1] User model — identity is the phone number")
user = memory.load(PHONE)
check("unknown user starts empty", user.is_new)
check("new user profile says so plainly", "new user" in user.describe().lower())

memory.remember_fact(PHONE, "budget", "400")
memory.remember_fact(PHONE, "allergies", "peanuts, shellfish")
memory.remember_fact(PHONE, "home", "Indiranagar")
memory.remember_food(PHONE, "Chicken Biryani", memory.ORDERED, venue="Meghana")
memory.remember_food(PHONE, "Chicken Biryani", memory.LIKED)
memory.remember_food(PHONE, "Cold Coffee", memory.DISLIKED)

user = memory.load(PHONE)
check("facts persisted", user.facts["budget"] == "400")
check("no longer a new user", not user.is_new)
check("favourites derived from food memory", "Chicken Biryani" in user.favourites())
check("dislikes feed the avoid list", "cold coffee" in user.avoids())
check("allergies feed the avoid list", "peanuts" in user.avoids())
check("profile surfaces MUST AVOID", "MUST AVOID" in user.describe())

memory.remember_fact(PHONE, "budget", "600")
check("fact update overwrites, no duplicate row", memory.load(PHONE).facts["budget"] == "600")
memory.remember_fact(PHONE, "home", "")
check("empty value forgets the fact", "home" not in memory.load(PHONE).facts)

try:
    memory.remember_food(PHONE, "x", "MAYBE")
    check("invalid sentiment rejected", False)
except ValueError:
    check("invalid sentiment rejected", True)

memory.record_turn(PHONE, "hi", "hello")
check("conversation history is durable", memory.load(PHONE).history[-1]["content"] == "hello")
check("history is oldest-first for prompting", memory.load(PHONE).history[0]["role"] == "user")

# ----------------------------------------------------------------------
print("\n[2] Provider abstraction — routed by capability, never by name")
registry.clear()
check("no providers -> capability unsupported", not registry.supports(ProviderKind.RESTAURANT))
check("unsupported search returns nothing", run(registry.search(ProviderKind.RESTAURANT, "biryani")) == ([], []))

grocery = FakeProvider("fake_grocery", ProviderKind.GROCERY, [offer("Milk", kind=ProviderKind.GROCERY)])
restaurant = FakeProvider("fake_resto", ProviderKind.RESTAURANT, [offer("Biryani")])
registry.register(grocery)
registry.register(restaurant)

check("registered kinds reported", set(registry.available_kinds()) == {ProviderKind.GROCERY, ProviderKind.RESTAURANT})
check("search routes to the matching kind only", len(run(registry.search(ProviderKind.GROCERY, "milk"))[0]) == 1)
check("grocery provider received the query", grocery.queries == ["milk"])
check("restaurant provider untouched by a grocery search", restaurant.queries == [])

second = FakeProvider("fake_resto_2", ProviderKind.RESTAURANT, [offer("Kebab")])
registry.register(second)
merged, _ = run(registry.search(ProviderKind.RESTAURANT, "dinner"))
check("multiple providers of one kind are merged", {o.title for o in merged} == {"Biryani", "Kebab"})

registry.clear()
registry.register(FakeProvider("broken", ProviderKind.RESTAURANT, [], fail=True))
registry.register(FakeProvider("healthy", ProviderKind.RESTAURANT, [offer("Pizza")]))
survived, broke = run(registry.search(ProviderKind.RESTAURANT, "food"))
check("one broken provider cannot break the turn", [o.title for o in survived] == ["Pizza"])
check("but the failure is reported, not swallowed", len(broke) == 1)

# ----------------------------------------------------------------------
print("\n[3] Offers never fabricate data")
bare = offer("Mystery Dish")
check("unknown rating stays None", bare.rating is None)
check("unknown eta stays None", bare.eta_minutes is None)
check("summary omits unknown fields", "rated" not in bare.summary() and "min" not in bare.summary())
rich = offer("Truffles Grilled Chicken", venue="Truffles", price=350, rating=4.7, eta_minutes=18)
check("summary states only real fields", "4.7" in rich.summary() and "18 min" in rich.summary())

# ----------------------------------------------------------------------
print("\n[4] Recommendation — deterministic, reasons are facts")
user = memory.load(PHONE)   # budget 600, avoids peanuts/shellfish/cold coffee
ranked = recommendation.rank([
    offer("Peanut Salad"),                                     # allergen
    offer("Cold Coffee"),                                      # disliked
    offer("Chicken Biryani", price=300),                       # favourite + in budget
    offer("Lobster Roll", price=900),                          # shellfish + over budget
    offer("Veg Pulao", price=200),
], user)

titles = [r.offer.title for r in ranked]
check("allergen named in the dish is filtered ('peanuts' -> 'Peanut Salad')", "Peanut Salad" not in titles)
check("disliked item filtered out", "Cold Coffee" not in titles)
# Honest ceiling: a name match cannot know lobster IS shellfish. That case is
# caught upstream by MUST AVOID in the system prompt, not by this filter.
check("allergy constraint always reaches the model", "shellfish" in user.describe().lower())
check("'ice' allergy must not filter 'rice'",
      [r.offer.title for r in recommendation.rank(
          [offer("Fried Rice")], memory.UserModel(phone="x", facts={"allergies": "ice"}))] == ["Fried Rice"])
check("favourite ranks first", titles[0] == "Chicken Biryani")
check("reason cites the real driver", any("regularly" in r for r in ranked[0].reasons))
check("budget reason references the stored budget", any("600" in r for r in ranked[0].reasons))

unavailable = recommendation.rank([offer("Sold Out", available=False)], user)
check("unavailable offers dropped", unavailable == [])

no_data = recommendation.rank([offer("Plain Thing")], memory.load("919000000001"))
check("unknown user still gets a ranking", len(no_data) == 1)
check("no invented reasons for a bare offer", no_data[0].reasons == ("matches what you asked for",))

rated = recommendation.rank([offer("A", rating=3.0), offer("B", rating=4.9)], user)
check("higher rating wins when that is the only signal", rated[0].offer.title == "B")

# ----------------------------------------------------------------------
def _find(_user, query, kind):
    """skills.find_food returns a SkillResult; these checks read its message."""
    async def go():
        return (await skills.find_food(identity.load(PHONE), query, kind)).message
    return go()


print("\n[5] Skills — anti-hallucination guard")
registry.clear()
registry.register(FakeProvider("fake_grocery", ProviderKind.GROCERY, [offer("Milk", kind=ProviderKind.GROCERY, price=60)]))

result = run(_find(user, "biryani", "restaurant"))
check("no restaurant provider -> CAPABILITY_UNAVAILABLE", result.startswith("CAPABILITY_UNAVAILABLE"))
check("model is explicitly told not to invent", "do not invent" in result.lower())

grocery_result = run(_find(user, "milk", "grocery"))
check("available capability returns real options", "Milk" in grocery_result)
check("results are marked as the only usable source", "ONLY these" in grocery_result)

check("unknown kind rejected", run(_find(user, "x", "spaceship")).startswith("ERROR"))

registry.clear()
registry.register(FakeProvider("empty", ProviderKind.RESTAURANT, []))
check("no results is distinct from no capability",
      "No restaurant results" in run(_find(user, "unicorn steak", "restaurant")))

registry.register(FakeProvider("allergen", ProviderKind.RESTAURANT, [offer("Peanut Curry")]))
filtered = run(_find(user, "curry", "restaurant"))
check("fully-filtered results explained honestly", "filtered out" in filtered)

# ----------------------------------------------------------------------
IDENTITY = identity.load(PHONE)
print("\n[6] Planner — tool dispatch never raises")
check("unknown tool reported, not raised",
      run(planner._dispatch(ToolCall("1", "no_such_tool", {}), IDENTITY, "m")).startswith("ERROR"))
check("missing required arg reported",
      run(planner._dispatch(ToolCall("1", "find_food", {}), IDENTITY, "m")).startswith("ERROR"))
check("bad sentiment surfaced as a tool error, not a crash",
      run(planner._dispatch(ToolCall("1", "remember_food", {"item": "x", "sentiment": "NOPE"}), IDENTITY, "m")).startswith("ERROR"))

run(planner._dispatch(ToolCall("1", "remember", {"key": "cuisine", "value": "South Indian"}), IDENTITY, "m"))
check("remember tool persists a fact", memory.load(PHONE).facts.get("cuisine") == "South Indian")
run(planner._dispatch(ToolCall("2", "remember_food", {"item": "Dosa", "sentiment": "ORDERED"}), IDENTITY, "m"))
check("remember_food tool persists", any(e["item"] == "Dosa" for e in memory.load(PHONE).food))

# ----------------------------------------------------------------------
print("\n[7] Planner — the LLM orchestrates, it does not reach past the tools")
calls = []


class FakeLLM:
    """Returns a scripted sequence of replies and records what it was asked."""

    def __init__(self, replies):
        self._replies = list(replies)

    async def chat(self, messages, tools=None, temperature=None):
        # Snapshot: the planner appends to this list as the turn proceeds.
        calls.append({"messages": list(messages), "tools": tools})
        return self._replies.pop(0)


registry.clear()
registry.register(FakeProvider("g", ProviderKind.GROCERY, [offer("Milk", kind=ProviderKind.GROCERY, price=60)]))

scripted = FakeLLM([
    LLMReply(tool_calls=[ToolCall("c1", "find_food", {"query": "milk", "kind": "grocery"})]),
    LLMReply(text="Grabbing you milk 🥛"),
])
planner.get_llm = lambda: scripted
reply = run(planner.plan(PHONE, "need milk"))

check("tool-calling turn resolves to text", reply == "Grabbing you milk 🥛")
check("two LLM round trips (call then answer)", len(calls) == 2)
check("tools were offered to the model", calls[0]["tools"] is not None)
check("system prompt carries the user profile", "MUST AVOID" in calls[0]["messages"][0]["content"])
check("durable history included in context", any(m.get("content") == "hello" for m in calls[0]["messages"]))
check("new user message is last", calls[0]["messages"][-1]["content"] == "need milk")
check("tool result fed back to the model",
      any(m.get("role") == "tool" for m in calls[1]["messages"]))

loop = FakeLLM([LLMReply(tool_calls=[ToolCall(str(i), "find_food", {"query": "x", "kind": "grocery"})])
                for i in range(planner.MAX_STEPS)] + [LLMReply(text="ok, done")])
planner.get_llm = lambda: loop
check("runaway tool loop is bounded", run(planner.plan(PHONE, "loop")) == "ok, done")

# ----------------------------------------------------------------------
print("\n[8] Concierge — failures stay friendly, turns persist")
planner.get_llm = lambda: FakeLLM([LLMReply(text="Sure thing!")])
check("empty message handled without an LLM call", run(concierge.respond(PHONE, "   ")) == concierge.EMPTY_MESSAGE_REPLY)

before = len(memory.load(PHONE).history)
check("normal turn returns the reply", run(concierge.respond(PHONE, "hey")) == "Sure thing!")
check("completed turn persisted (user + assistant)", len(memory.load(PHONE).history) == before + 2)


class ExplodingLLM:
    async def chat(self, *a, **kw):
        raise RuntimeError("groq is down")


planner.get_llm = lambda: ExplodingLLM()
before = len(memory.load(PHONE).history)
check("LLM failure -> friendly reply, no stack trace", run(concierge.respond(PHONE, "hi")) == concierge.ERROR_REPLY)
check("failed turn NOT persisted (cannot poison context)", len(memory.load(PHONE).history) == before)

# ----------------------------------------------------------------------
print("\n[9] Malformed tool arguments never crash a turn")
check("non-JSON arguments -> empty dict", _parse_arguments("not json{{") == {})
check("JSON array arguments -> empty dict", _parse_arguments("[1,2]") == {})
check("empty arguments -> empty dict", _parse_arguments("") == {})
check("valid arguments parsed", _parse_arguments('{"a": 1}') == {"a": 1})

# ----------------------------------------------------------------------
print("\n[10] ARCHITECTURE: no platform name leaks above the provider layer")
root = pathlib.Path(__file__).resolve().parent.parent
leaks = []
for path in list(root.glob("ai/*.py")) + list(root.glob("backend/*.py")) + list(root.glob("core/*.py")):
    text = path.read_text(encoding="utf-8", errors="ignore").lower()
    # MIGRATION.md-style prose is fine; an import or attribute reference is not.
    for marker in ("import swiggy", "from integrations", "swiggyinstamart", "instamart"):
        if marker in text:
            leaks.append(f"{path.relative_to(root)} -> {marker}")
check(f"no Swiggy references outside ai/providers/ {leaks or ''}", not leaks)

allowed = (root / "ai" / "providers" / "swiggy.py").read_text(encoding="utf-8")
check("the provider adapter does own the platform detail", "SwiggyInstamart" in allowed)

print("\n" + "=" * 70)
print(f"RESULT: {_passed} passed, {_failed} failed")
try:
    os.unlink(_tmp.name)
except OSError:
    pass
sys.exit(1 if _failed else 0)
