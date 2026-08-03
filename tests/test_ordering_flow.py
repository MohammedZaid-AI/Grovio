"""
The ordering flow, end to end, for BOTH providers.

    "I want biryani"  -> numbered list with prices -> "1" -> ordered
    "I need milk"     -> numbered list with prices -> "2" -> ordered

The experience must be IDENTICAL either side: search, numbered list, selection,
order — and a selection must never start a second search.

Everything below runs against fake MCP clients. Nothing here touches Swiggy and
nothing here places a real order.

The payload sections pin regressions introduced during the ERP -> concierge
pivot and found by diffing against ai/services/swiggy_service.py at 06a1204^:

  * checkout wraps the order under `data` but leaves the id at the TOP level;
    returning one half lost the id and reported a PLACED order as failed
  * the total is `cartTotal`, which the rewrite dropped
  * a non-error checkout means the order EXISTS — raising because the id could
    not be parsed sent the user into the retry flow and risked ordering twice
  * price and skuId live on the VARIANT, not the product
  * `quantityDescription` is the pack size the listing showed
"""
import asyncio
import os
import sys
import tempfile

sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from cryptography.fernet import Fernet

os.environ["TOKEN_ENCRYPTION_KEY"] = Fernet.generate_key().decode()
os.environ["WHATSAPP_TRANSPORT"] = "cloud"

import db

_tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
_tmp.close()
db.DB_PATH = _tmp.name
db.init_db()

from ai import conversation, identity, planner, skills
from ai.providers import ProviderKind, registry, swiggy, swiggy_food
from ai.providers.base import ItemUnavailable, ProviderError, SearchContext
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


class Result:
    """Stand-in for an MCP tool result."""

    def __init__(self, is_error=False, structured=None, text=None):
        self.isError = is_error
        self.structuredContent = structured
        self.content = [type("C", (), {"text": text})()] if text else None


# ======================================================================
# Fake MCP clients — the real response shapes, no network
# ======================================================================
RESTAURANT_PAYLOAD = {
    "items": [
        {
            "name": "Chicken Biryani", "itemId": "IT-1",
            "price": {"finalPrice": 340},
            "restaurant": {"id": "RES-1", "name": "Meghana Foods",
                           "avgRating": 4.6, "sla": {"deliveryTime": 22}},
        },
        {
            "name": "Mutton Biryani", "itemId": "IT-2", "price": 280,
            "restaurant": {"id": "RES-2", "name": "Empire Restaurant",
                           "avgRating": 4.3, "sla": {"deliveryTime": 18}},
        },
    ]
}

# The other shape Swiggy uses: restaurant cards, each holding its dishes.
GROUPED_PAYLOAD = {
    "cards": [
        {
            "id": "RES-9", "name": "Nagarjuna", "avgRating": 4.4,
            "sla": {"deliveryTime": 31},
            "items": [{"name": "Andhra Biryani", "itemId": "IT-9", "price": 410}],
        }
    ]
}

GROCERY_PRODUCTS = [
    {
        "displayName": "Amul Taaza Milk", "brand": "Amul", "skuId": "SKU-TOP",
        "variations": [{"spinId": "SPIN-1", "skuId": "SKU-1",
                        "price": {"offerPrice": 33, "mrp": 35},
                        "quantityDescription": "500 ml", "inStock": True}],
    },
    {
        "displayName": "Nandini Toned Milk", "brand": "Nandini",
        "variations": [{"spinId": "SPIN-2", "skuId": "SKU-2",
                        "price": {"offerPrice": 26},
                        "quantityDescription": "500 ml", "inStock": True}],
    },
]


class FoodClient:
    """Fake Swiggy Food MCP client."""

    def __init__(self, payload=None, checkout=None, cart_error=False):
        self.payload = payload if payload is not None else RESTAURANT_PAYLOAD
        self.checkout = checkout or Result(
            structured={"orderId": "FOOD-77", "data": {"cartTotal": 340, "eta": 24}}
        )
        self.cart_error = cart_error
        self.searches = 0
        self.carts = []

    def supports(self, key):
        return True

    async def default_address_id(self):
        return "addr-food"

    async def search_dishes(self, query, address_id, offset=0):
        self.searches += 1
        return self.payload

    async def add_to_cart(self, address_id, restaurant_id, cart_items, restaurant_name=None):
        self.carts.append((restaurant_id, cart_items))
        return Result(is_error=self.cart_error,
                      text="Item is Out of Stock" if self.cart_error else None)

    async def place_order(self, address_id, payment_method="Cash"):
        return self.checkout


class GroceryClient:
    """Fake Swiggy Instamart MCP client."""

    def __init__(self, products=None, checkout=None, cart_items=None):
        self.products = GROCERY_PRODUCTS if products is None else products
        self.checkout_result = checkout or Result(
            structured={"orderId": "IM-42", "data": {"cartTotal": 26, "etaMinutes": 11}}
        )
        self.cart_items = [{"x": 1}] if cart_items is None else cart_items
        self.searches = 0
        self.added = []

    async def get_address_id(self):
        return "addr-im"

    async def get_product_options(self, query):
        self.searches += 1
        return self.products

    async def clear_cart(self):
        return Result()

    async def update_cart(self, address_id, items):
        self.added.append(items)
        return Result()

    async def get_cart(self):
        return Result(structured={"items": self.cart_items})

    async def checkout(self, address_id, payment_method=None):
        return self.checkout_result


def food_provider(client):
    provider = swiggy_food.SwiggyFoodProvider()
    provider._client = client
    return provider


def grocery_provider(client):
    provider = swiggy.SwiggyInstamartProvider()
    provider._client = client
    return provider


# ======================================================================
print("\n[1] RESTAURANT search — a numbered list with name, price, ETA, rating")
food = FoodClient()
offers = run(food_provider(food).search("biryani", SearchContext(limit=10)))

check("dishes come back as offers", len(offers) == 2)
check("title is the dish", offers[0].title == "Chicken Biryani")
check("venue is the restaurant", offers[0].venue == "Meghana Foods")
check("PRICE from a nested price object", offers[0].price == 340)
check("PRICE from a flat number", offers[1].price == 280)
check("rating read off the restaurant, not the dish", offers[0].rating == 4.6)
check("ETA read off the restaurant's sla", offers[0].eta_minutes == 22)
check("ordering id packs restaurant AND item",
      swiggy_food._unpack_id(offers[0].id) == ("RES-1", "IT-1"))

summary = offers[0].summary()
check("summary shows a ₹ price", "₹340" in summary)
check("summary never says N/A", "N/A" not in summary)
check("summary carries the rating and ETA", "4.6" in summary and "22 min" in summary)

grouped = run(food_provider(FoodClient(GROUPED_PAYLOAD)).search("biryani", SearchContext()))
check("REGRESSION: restaurant-grouped cards are flattened, not dropped", len(grouped) == 1)
check("grouped dish keeps its price", grouped[0].price == 410)
check("grouped dish inherits the card's restaurant id",
      swiggy_food._unpack_id(grouped[0].id) == ("RES-9", "IT-9"))
check("grouped dish inherits the card's rating", grouped[0].rating == 4.4)

# ======================================================================
print("\n[2] GROCERY search — a numbered list with name, brand, price, quantity")
grocery = GroceryClient()
items = run(grocery_provider(grocery).search("milk", SearchContext(limit=10)))

check("products come back as offers", len(items) == 2)
check("title is the product", items[0].title == "Amul Taaza Milk")
check("venue is the brand", items[0].venue == "Amul")
check("PRICE from the nested variant price object", items[0].price == 33)
check("REGRESSION: pack size (quantityDescription) survives", items[0].tags == ("500 ml",))
check("summary shows a ₹ price", "₹33" in items[0].summary())
check("summary shows the pack size", "500 ml" in items[0].summary())
check("summary never says N/A", "N/A" not in items[0].summary())

check("REGRESSION: variant price WINS over a product-level one",
      swiggy._price_of({"price": 999, "variations": [{"price": {"offerPrice": 33}}]}) == 33)
check("REGRESSION: variant skuId wins over the product-level one",
      swiggy._pick_variant_first(GROCERY_PRODUCTS[0], "skuId") == "SKU-1")

# ======================================================================
print("\n[3] Both providers place an order from a selection")
food = FoodClient()
placed = run(food_provider(food).place(offers[0], 1, SearchContext()))
check("restaurant order returns the provider's id", placed.order_id == "FOOD-77")
check("REGRESSION: cartTotal is read as the total", placed.total == 340)
check("cart was built with the restaurant id", food.carts[0][0] == "RES-1")
check("cart carries the item id and quantity",
      food.carts[0][1] == [{"itemId": "IT-1", "quantity": 1}])

grocery = GroceryClient()
placed = run(grocery_provider(grocery).place(items[1], 2, SearchContext()))
check("grocery order returns the provider's id", placed.order_id == "IM-42")
check("REGRESSION: cartTotal is read as the total", placed.total == 26)
check("cart carries spinId, skuId and quantity",
      grocery.added[0] == [{"spinId": "SPIN-2", "quantity": 2, "skuId": "SKU-2"}])

# ======================================================================
print("\n[4] REGRESSION: an accepted order is never reported as failed")
# Swiggy wraps the body under `data` but leaves the id at the top level.
split = FoodClient(checkout=Result(structured={"orderId": "SPLIT-1",
                                               "data": {"cartTotal": 340}}))
placed = run(food_provider(split).place(offers[0], 1, SearchContext()))
check("id at the top level survives the `data` unwrap", placed.order_id == "SPLIT-1")
check("total from inside `data` survives too", placed.total == 340)

# Accepted, but the id is somewhere we can't parse. The order EXISTS.
for label, provider, client in (
    ("restaurant", food_provider, FoodClient(checkout=Result(structured={"ok": True}))),
    ("grocery", grocery_provider, GroceryClient(checkout=Result(structured={"ok": True}))),
):
    offer = offers[0] if label == "restaurant" else items[0]
    try:
        result = run(provider(client).place(offer, 1, SearchContext()))
        check(f"{label}: accepted-but-unparseable is PLACED, not an exception",
              result.status == "PLACED")
        check(f"{label}: the missing id is empty, never invented", result.order_id == "")
    except Exception as e:
        check(f"{label}: accepted-but-unparseable is PLACED, not an exception", False)
        check(f"{label}: the missing id is empty, never invented", False)

# A human-readable confirmation must not blow up the JSON parser.
prose = FoodClient(checkout=Result(text="Your order has been placed."))
placed = run(food_provider(prose).place(offers[0], 1, SearchContext()))
check("a prose confirmation still counts as placed", placed.status == "PLACED")

# ======================================================================
print("\n[5] REGRESSION: a sold-out item is not retried three times")
sold_out = FoodClient(cart_error=True)
try:
    run(food_provider(sold_out).place(offers[0], 1, SearchContext()))
    check("out-of-stock raises ItemUnavailable, not a generic error", False)
except ItemUnavailable:
    check("out-of-stock raises ItemUnavailable, not a generic error", True)
except ProviderError:
    check("out-of-stock raises ItemUnavailable, not a generic error", False)

check("error text is classified as an item problem",
      swiggy_food._is_item_problem("Item is Out of Stock"))
check("a transport failure is NOT an item problem",
      not swiggy_food._is_item_problem("upstream timeout"))
check("grocery classifies the same vocabulary",
      swiggy._is_item_problem("Max Per Item Quantity Limit reached"))

# ======================================================================
# Full conversational flow, both providers, through the planner
# ======================================================================
class Recorder:
    """An LLM that just phrases whatever it is handed, and counts turns."""

    def __init__(self):
        self.turns = []

    async def chat(self, messages, tools=None, temperature=None):
        self.turns.append({"messages": list(messages), "tools": tools})
        return LLMReply(text="ok")


def show(phone, provider_name, kind, entries):
    """Put a numbered list on the table exactly as find_food would."""
    conversation.reset(phone)
    conversation.show_offers(phone, [
        {"provider": provider_name, "id": i, "title": t, "venue": v,
         "price": p, "currency": "INR", "eta_minutes": e, "kind": kind}
        for i, t, v, p, e in entries
    ], "biryani")


class Spy:
    """A provider that records every call, so a stray search is visible."""

    def __init__(self, name, kind, fail_times=0):
        self.name = name
        self.display_name = name
        self.kind = kind
        self.supports_tracking = False
        self.supports_cancellation = False
        self.searches = 0
        self.placed = []
        self.fail_times = fail_times

    async def search(self, query, ctx):
        self.searches += 1
        return []

    async def place(self, offer, quantity, ctx):
        from ai.providers.base import PLACED, PlacedOrder
        if self.fail_times > 0:
            self.fail_times -= 1
            raise ProviderError("kitchen busy")
        self.placed.append((offer.id, quantity))
        return PlacedOrder(provider=self.name, order_id=f"{self.name}-1",
                           status=PLACED, total=offer.price, eta_minutes=20)


for label, provider_name, kind, entries, pick, expect in (
    ("RESTAURANT", "spy_food", ProviderKind.RESTAURANT.value,
     [("IT-1", "Chicken Biryani", "Meghana Foods", 340, 22),
      ("IT-2", "Mutton Biryani", "Empire Restaurant", 280, 18)],
     "1", "IT-1"),
    ("GROCERY", "spy_grocery", ProviderKind.GROCERY.value,
     [("SPIN-1::SKU-1", "Amul Taaza Milk", "Amul", 33, None),
      ("SPIN-2::SKU-2", "Nandini Toned Milk", "Nandini", 26, None)],
     "2", "SPIN-2::SKU-2"),
):
    print(f"\n[6] {label}: selection -> order, with no second search")
    phone = f"9199888{len(label)}001"
    registry.clear()
    spy = Spy(provider_name, ProviderKind(kind))
    registry.register(spy)
    identity.load(phone)
    show(phone, provider_name, kind, entries)

    recorder = Recorder()
    planner.get_llm = lambda: recorder
    run(planner.plan(phone, pick))

    check(f"{label}: the selection placed an order", len(spy.placed) == 1)
    check(f"{label}: it ordered the option they picked", spy.placed[0][0] == expect)
    check(f"{label}: NO second search was triggered", spy.searches == 0)
    check(f"{label}: the model was never offered tools for this turn",
          all(turn["tools"] is None for turn in recorder.turns))
    check(f"{label}: state moved to ORDER_COMPLETE",
          conversation.load(phone).state == conversation.State.ORDER_COMPLETE)

    order = db.get_latest_order(phone)
    check(f"{label}: the order was persisted", order is not None)
    check(f"{label}: with the provider's own order id",
          order["provider_order_id"] == f"{provider_name}-1")

    # ---- retry ----
    print(f"\n[7] {label}: a failed order retries the SAME item")
    phone += "9"
    registry.clear()
    spy = Spy(provider_name, ProviderKind(kind), fail_times=1)
    registry.register(spy)
    identity.load(phone)
    show(phone, provider_name, kind, entries)

    planner.get_llm = lambda: Recorder()
    run(planner.plan(phone, pick))
    state = conversation.load(phone)
    check(f"{label}: a failure waits on the user, it does not give up",
          state.state == conversation.State.AWAITING_RETRY_CONFIRMATION)
    check(f"{label}: the chosen item is remembered", state.pending.offer_id == expect)
    check(f"{label}: the provider is remembered", state.pending.provider == provider_name)
    check(f"{label}: REGRESSION: the KIND is remembered too", state.pending.kind == kind)

    recorder = Recorder()
    planner.get_llm = lambda: recorder
    run(planner.plan(phone, "yes"))

    check(f"{label}: 'yes' retried and the order went through", len(spy.placed) == 1)
    check(f"{label}: it retried the SAME item", spy.placed[0][0] == expect)
    check(f"{label}: retrying did NOT search again", spy.searches == 0)
    check(f"{label}: the retry never consulted the model for a decision",
          all(turn["tools"] is None for turn in recorder.turns))
    check(f"{label}: ends in ORDER_COMPLETE",
          conversation.load(phone).state == conversation.State.ORDER_COMPLETE)

# ======================================================================
print("\n[8] Every affirmative phrasing retries the same order")
for phrase in ("yes", "Yes please", "retry", "go ahead", "Please do", "try again", "sure"):
    phone = "919155500001"
    registry.clear()
    spy = Spy("spy_food", ProviderKind.RESTAURANT, fail_times=1)
    registry.register(spy)
    identity.load(phone)
    show(phone, "spy_food", ProviderKind.RESTAURANT.value,
         [("IT-1", "Chicken Biryani", "Meghana Foods", 340, 22)])

    planner.get_llm = lambda: Recorder()
    run(planner.plan(phone, "1"))
    run(planner.plan(phone, phrase))
    check(f"{phrase!r} retried the same order", spy.placed and spy.placed[0][0] == "IT-1")
    check(f"{phrase!r} did not search again", spy.searches == 0)

# ======================================================================
print("\n[9] Picking a DIFFERENT option after a failure still never searches")
phone = "919155500002"
registry.clear()
spy = Spy("spy_food", ProviderKind.RESTAURANT, fail_times=99)
registry.register(spy)
identity.load(phone)
show(phone, "spy_food", ProviderKind.RESTAURANT.value,
     [("IT-1", "Chicken Biryani", "Meghana Foods", 340, 22),
      ("IT-2", "Mutton Biryani", "Empire Restaurant", 280, 18)])

planner.get_llm = lambda: Recorder()
run(planner.plan(phone, "1"))                 # fails
run(planner.plan(phone, "yes"))               # retry, fails
run(planner.plan(phone, "yes"))               # retry, fails -> exhausted
state = conversation.load(phone)
check("retries are bounded, not infinite", state.state == conversation.State.ORDER_FAILED)
check("the options are still on the table", len(state.offers) == 2)

spy.fail_times = 0
run(planner.plan(phone, "2"))
check("REGRESSION: option 2 was ordered straight from the dead list",
      spy.placed and spy.placed[-1][0] == "IT-2")
check("still no search", spy.searches == 0)

# ======================================================================
print("\n[10] A stale list cannot be ordered from")
phone = "919155500003"
registry.clear()
spy = Spy("spy_food", ProviderKind.RESTAURANT)
registry.register(spy)
identity.load(phone)
show(phone, "spy_food", ProviderKind.RESTAURANT.value,
     [("IT-1", "Chicken Biryani", "Meghana Foods", 340, 22)])
db.save_conversation_state(phone=phone, state=conversation.State.AWAITING_SELECTION,
                           offers=None, query="biryani", pending=None)

result = run(skills.place_order(identity.load(phone), 1))
check("ordering from an empty list is refused", result.status == skills.SkillStatus.STALE)
check("nothing was ordered", not spy.placed)

print("\n" + "=" * 70)
print(f"RESULT: {_passed} passed, {_failed} failed")
try:
    os.unlink(_tmp.name)
except OSError:
    pass
sys.exit(1 if _failed else 0)
