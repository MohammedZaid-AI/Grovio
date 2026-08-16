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

# Authorisation is enforced in concierge.respond; these suites are about
# everything else, so put their numbers on the allowlist.
os.environ["AUTHORIZED_PHONES"] = "919876500000,919800000001"

import db

_tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
_tmp.close()
db.DB_PATH = _tmp.name
db.init_db()

from ai import concierge, conversation, identity, memory, planner, recommendation, skills
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


# ----------------------------------------------------------------------
print("\n[12] ANYONE may chat; only authorised numbers may SPEND")
# core/authz.is_authorized_user shipped with no callers at all, so ordering was
# ungated. It guards MONEY, not conversation: its own docstring says "authorized
# to spend money — i.e. to place an order". Recommending food to a stranger
# costs nothing; ordering it puts food at the account owner's door, cash on
# delivery, for them to pay.
from core import authz

_saved_allow = os.environ.get("AUTHORIZED_PHONES", "")
os.environ["AUTHORIZED_PHONES"] = "919800000001"

planner.get_llm = lambda: FakeLLM([LLMReply(text="here you go")])
check("an allowed number is answered",
      run(concierge.respond("919800000001", "hi")) == "here you go")

planner.get_llm = lambda: FakeLLM([LLMReply(text="try the biryani")])
check("a STRANGER is answered too — chat is open to anyone",
      run(concierge.respond("910000009999", "what's good?")) == "try the biryani")

os.environ["AUTHORIZED_PHONES"] = ""
planner.get_llm = lambda: FakeLLM([LLMReply(text="still chatting")])
check("an unset allowlist does NOT silence the concierge",
      run(concierge.respond("919800000001", "hi")) == "still chatting")

# ...but the money path is gated, at the single point every order passes through.
registry.clear()
spender = FakeProvider("pay_provider", ProviderKind.GROCERY,
                       [offer("Milk", kind=ProviderKind.GROCERY, price=33)])
spender.placed = []


async def _place(offer_, quantity, ctx):
    spender.placed.append(offer_.id)
    from ai.providers.base import PLACED, PlacedOrder
    return PlacedOrder(provider=spender.name, order_id="OK-1", status=PLACED, total=33)


spender.place = _place
registry.register(spender)

STRANGER = "910000009999"
identity.load(STRANGER)
conversation.show_offers(STRANGER, [{"provider": "pay_provider", "id": "SKU-1",
                                     "title": "Milk", "venue": "Amul", "price": 33,
                                     "currency": "INR", "eta_minutes": None,
                                     "kind": "grocery"}], "milk")

os.environ["AUTHORIZED_PHONES"] = "919800000001"
result = run(skills.place_order(identity.load(STRANGER), 1))
check("an unauthorised number CANNOT order", result.status == skills.SkillStatus.ERROR)
check("nothing reached the provider", spender.placed == [])
check("the model is told to stay helpful about the food",
      "can't place the order" in result.message or "NOT AUTHORISED" in result.message)
check("and is told NOT to claim success", "Do NOT claim an order was" in result.message)

os.environ["AUTHORIZED_PHONES"] = ""
conversation.show_offers(STRANGER, [{"provider": "pay_provider", "id": "SKU-1",
                                     "title": "Milk", "venue": "Amul", "price": 33,
                                     "currency": "INR", "eta_minutes": None,
                                     "kind": "grocery"}], "milk")
result = run(skills.place_order(identity.load(STRANGER), 1))
check("FAILS CLOSED — an unset allowlist lets NOBODY spend",
      result.status == skills.SkillStatus.ERROR and spender.placed == [])

os.environ["AUTHORIZED_PHONES"] = "+91 00000 09999"
conversation.show_offers(STRANGER, [{"provider": "pay_provider", "id": "SKU-1",
                                     "title": "Milk", "venue": "Amul", "price": 33,
                                     "currency": "INR", "eta_minutes": None,
                                     "kind": "grocery"}], "milk")
result = run(skills.place_order(identity.load(STRANGER), 1))
check("an authorised number CAN order", result.ok and spender.placed == ["SKU-1"])
check("allowlist entries match on digits, whatever the formatting", result.ok)
os.environ["AUTHORIZED_PHONES"] = _saved_allow

# ----------------------------------------------------------------------
print("\n[13] Optional tool arguments accept an explicit null")
# Groq/OpenAI validate the model's OWN generated tool call against the schema we
# send. Models routinely emit `"max_price": null` for an omitted optional, and a
# bare {"type": "number"} makes the provider 400 the call before we ever see it,
# killing the whole turn. Observed live: tool_use_failed on find_food.
for tool in planner.TOOLS:
    fn = tool["function"]
    params = fn.get("parameters", {})
    required = set(params.get("required", []))
    for name, spec in (params.get("properties") or {}).items():
        if name in required:
            continue
        kind = spec.get("type")
        check(f"{fn['name']}.{name} (optional) accepts null",
              isinstance(kind, list) and "null" in kind)
# ======================================================================
print("\n[14] Recommendations behave like a friend, not a search box")
FRIEND = "919555000111"
memory.remember_fact(FRIEND, "budget", "400")
memory.remember_food(FRIEND, "Chicken Biryani", memory.ORDERED, venue="Meghana")
friend = memory.load(FRIEND)


def dish(title, venue, price=300, rating=None, eta=None):
    return offer(title, venue=venue, price=price, rating=rating, eta_minutes=eta,
                 id=f"{venue}:{title}")


# Ratings on a delivery app cluster in a narrow band. Adding the raw number made
# rating dominate the total while barely separating anything.
ranked = recommendation.rank([
    dish("Pizza A", "Alpha", rating=4.6, eta=20),
    dish("Pizza B", "Beta", rating=4.0, eta=20),
], friend)
check("a 4.6 beats a 4.0 decisively", ranked[0].offer.venue == "Alpha")
check("and the gap is meaningful, not noise",
      ranked[0].score - ranked[1].score > 0.9)

# A friend suggests somewhere further when it is genuinely better.
further = recommendation.rank([
    dish("Near Pizza", "Close", rating=3.9, eta=15),
    dish("Far Pizza", "Distant", rating=4.7, eta=40),
], friend)
check("further-but-better outranks near-but-average",
      further[0].offer.venue == "Distant")
check("and the trade-off is SPOKEN, not buried",
      any("rather than round the corner" in r for r in further[0].reasons))
check("the reason names the real ETA", any("40 min" in r for r in further[0].reasons))

# Distance is never a penalty — only closeness is a bonus.
slow = dish("Slow", "S", rating=4.0, eta=90)
fast = dish("Fast", "F", rating=4.0, eta=10)
check("being far away is never punished, only nearness rewarded",
      recommendation._speed_score(slow) == 0.0
      and recommendation._speed_score(fast) > 0)

# Five dishes from one kitchen is a menu, not a choice.
same = [dish(f"Pizza {i}", "OnePlace", rating=4.5, eta=20) for i in range(5)]
elsewhere = [dish("Burger", "Other", rating=4.4, eta=25),
             dish("Pasta", "Third", rating=4.4, eta=25)]
varied = recommendation.rank(same + elsewhere, friend, limit=4)
venues = [r.offer.venue for r in varied]
check("no more than two dishes from one venue",
      venues.count("OnePlace") <= recommendation.MAX_PER_VENUE)
check("other places get a look in", len(set(venues)) >= 2)

# Novelty, gently — it breaks ties, it does not steer the list.
novel = recommendation.rank([
    dish("Chicken Biryani", "Meghana", rating=4.2, eta=25),
    dish("Thai Green Curry", "New Place", rating=4.2, eta=25),
], friend)
check("the regular order still wins on a tie", novel[0].offer.title == "Chicken Biryani")
check("but the new thing is flagged as new",
      any("haven't tried" in r for r in novel[1].reasons))

# Everything a reason says still has to be true.
plain = recommendation.rank([dish("Mystery", "X", rating=None, eta=None)], friend)
check("no rating means no rating is claimed",
      not any("rated" in r for r in plain[0].reasons))
check("no ETA means no ETA is claimed",
      not any("min" in r for r in plain[0].reasons))
check("more options are offered by default", recommendation.rank.__defaults__[0] == 6)

# Two options scoring 2.65 and 2.6500000000000004 are the same option to anyone
# eating dinner. Letting float noise pick the order makes it unreproducible.
tie_a = dish("Tie A", "First", rating=4.3, eta=18)
tie_b = dish("Tie B", "Second", rating=4.4, eta=26)
order = [r.offer.title for r in recommendation.rank([tie_a, tie_b], friend)]
check("a near-tie keeps the provider's own order, every time",
      all([r.offer.title for r in recommendation.rank([tie_a, tie_b], friend)] == order
          for _ in range(5)))
check("and that order is the input order, not float luck", order[0] == "Tie A")
print("\n" + "=" * 70)
print(f"RESULT: {_passed} passed, {_failed} failed")
try:
    os.unlink(_tmp.name)
except OSError:
    pass
sys.exit(1 if _failed else 0)
