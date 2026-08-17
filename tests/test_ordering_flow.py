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
os.environ["MCP_USE_ANONYMIZED_TELEMETRY"] = "false"
os.environ.setdefault("WHATSAPP_ACCESS_TOKEN", "test-token")
os.environ.setdefault("WHATSAPP_PHONE_NUMBER_ID", "555000111")

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

# What search_menu ACTUALLY returns. Key names captured from the live server on
# 2026-08-16 — flat entries, snake_case ids, no nested restaurant object and no
# ETA field at all. Every dish was being dropped because `menu_item_id` was not
# in the id lookup, and `restaurant_name` was not in the venue lookup.
LIVE_MENU_PAYLOAD = {
    "items": [
        {"name": "Chicken Biryani", "price": 340, "menu_item_id": "MI-1",
         "inStock": True, "imageUrl": "x.jpg", "restaurant_id": "R-1",
         "restaurant_name": "Meghana Foods", "rating": 4.6, "hasAddons": True},
        {"name": "Mutton Biryani", "price": 280, "menu_item_id": "MI-2",
         "inStock": True, "imageUrl": "y.jpg", "restaurant_id": "R-2",
         "restaurant_name": "Empire Restaurant", "rating": 4.3},
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

    def __init__(self, payload=None, checkout=None, cart_error=False,
                 payment_options=None, statuses=None):
        self.payload = payload if payload is not None else RESTAURANT_PAYLOAD
        self.checkout = checkout or Result(
            structured={"orderId": "FOOD-77", "data": {"cartTotal": 340, "eta": 24}}
        )
        self.cart_error = cart_error
        # Default: cash is still offered, as Swiggy's docs describe it.
        self.options = payment_options if payment_options is not None else {"cod": True}
        self.statuses = list(statuses or [])
        self.searches = 0
        self.carts = []
        self.calls = []
        self.paid_with = []
        self.confirmed = []

    def supports(self, key):
        return True

    async def default_address_id(self):
        return "addr-food"

    async def search_dishes(self, query, address_id, offset=0):
        self.searches += 1
        return self.payload

    async def call(self, key, args):
        self.calls.append(key)
        return Result(structured={"items": [{"menu_item_id": "IT-1"}]})

    async def add_to_cart(self, address_id, restaurant_id, cart_items, restaurant_name=None):
        self.carts.append((restaurant_id, cart_items))
        return Result(is_error=self.cart_error,
                      text="Item is Out of Stock" if self.cart_error else None)

    async def payment_options(self, address_id):
        return self.options

    async def place_order(self, address_id, payment_method="Cash",
                          intent_app=None, generate_upi_qr=False):
        self.paid_with.append({"method": payment_method, "intent_app": intent_app,
                               "qr": generate_upi_qr})
        return self.checkout

    async def payment_status(self, order_id, paas_id, address_id):
        return {"status": self.statuses.pop(0) if self.statuses else "pending"}

    async def confirm_order(self, order_id, address_id, paas_id=None):
        self.confirmed.append(order_id)
        return {"ok": True}


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

print("\n[1b] REGRESSION: the shape search_menu really returns")
live = run(food_provider(FoodClient(LIVE_MENU_PAYLOAD)).search("biryani", SearchContext(limit=10)))
check("live-shape dishes are orderable (menu_item_id)", len(live) == 2)
check("restaurant_id is read", swiggy_food._unpack_id(live[0].id)[0] == "R-1")
check("menu_item_id is read", swiggy_food._unpack_id(live[0].id)[1] == "MI-1")
check("venue is the RESTAURANT, not the dish's own name",
      live[0].venue == "Meghana Foods")
check("the dish name stays the title", live[0].title == "Chicken Biryani")
check("price is read", live[0].price == 340)
check("rating is read", live[0].rating == 4.6)
check("no ETA field means no ETA — never invented", live[0].eta_minutes is None)
check("summary shows venue and price, omits the missing ETA",
      "Meghana Foods" in live[0].summary() and "₹340" in live[0].summary()
      and "min" not in live[0].summary())

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
sent_item = food.carts[0][1][0]
check("cart carries the quantity", sent_item["quantity"] == 1)
# Both spellings go out: search_menu returns `menu_item_id`, and the cart tool's
# argument shape is not published in the tool listing. An item the cart silently
# ignores surfaces only as "Some error while creating the order" at checkout.
check("cart carries the item id as menu_item_id", sent_item["menu_item_id"] == "IT-1")
check("cart also carries it as itemId", sent_item["itemId"] == "IT-1")

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
    """Put a numbered list on the table exactly as find_food would.

    Also authorises the phone to spend. Ordering is gated by AUTHORIZED_PHONES
    (proved in test_concierge.py §12); this suite is about ordering MECHANICS,
    so every test phone is allowed through that gate here.
    """
    existing = os.environ.get("AUTHORIZED_PHONES", "")
    os.environ["AUTHORIZED_PHONES"] = f"{existing},{phone}" if existing else phone
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

# ======================================================================
print("\n[11] A PLACED order is never reported as failed")
# The bug that shipped: the live database still had the pre-pivot ERP `orders`
# table, so save_order raised "no such column: provider" AFTER Swiggy had
# accepted the order. The exception escaped plan() and the user was told it
# failed — for an order sitting in their Swiggy account.
import sqlite3

legacy = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
legacy.close()
raw = sqlite3.connect(legacy.name)
raw.execute("CREATE TABLE orders (id INTEGER PRIMARY KEY, product_name TEXT, "
            "spin_id TEXT, quantity INTEGER, order_type TEXT, status TEXT, "
            "total REAL, phone TEXT)")
raw.execute("INSERT INTO orders (product_name, phone) VALUES ('ERP leftover', '91')")
raw.commit()
raw.close()

_live = db.DB_PATH
db.DB_PATH = legacy.name
db.init_db()

columns = {r[1] for r in sqlite3.connect(legacy.name).execute("PRAGMA table_info(orders)")}
check("the ERP orders table is retired, not reused", "provider_order_id" in columns)
check("the concierge schema is what remains", "provider" in columns and "venue" in columns)
kept = sqlite3.connect(legacy.name).execute(
    "SELECT product_name FROM orders_legacy_erp").fetchone()
check("ERP rows are preserved, not destroyed", kept[0] == "ERP leftover")
check("save_order now works", db.save_order(
    phone="91", provider="p", provider_order_id="X-1", status="PLACED", title="Milk") > 0)

db.init_db()   # must be idempotent — a second run cannot retire the good table
check("re-running init_db leaves the concierge table alone",
      db.get_latest_order("91")["provider_order_id"] == "X-1")
db.DB_PATH = _live

# Now the guarantee itself: bookkeeping fails, the order still stands.
for label, break_it in (
    ("save_order", "save_order"),
    ("conversation close", "order_succeeded"),
    ("food memory", "remember_food"),
):
    phone = f"91977700{len(label)}"
    registry.clear()
    spy = Spy("spy_grocery", ProviderKind.GROCERY)
    registry.register(spy)
    identity.load(phone)
    show(phone, "spy_grocery", ProviderKind.GROCERY.value,
         [("SPIN-1::SKU-1", "Amul Taaza Milk", "Amul", 33, None)])

    def explode(*a, **k):
        raise sqlite3.OperationalError("table orders has no column named provider")

    from ai import memory as _memory
    targets = {"save_order": (db, "save_order"),
               "order_succeeded": (conversation, "order_succeeded"),
               "remember_food": (_memory, "remember_food")}
    module, attr = targets[break_it]
    original = getattr(module, attr)
    setattr(module, attr, explode)
    try:
        result = run(skills.place_order(identity.load(phone), 1))
    finally:
        setattr(module, attr, original)

    check(f"{label} failure still reports the order as PLACED",
          result.status == skills.SkillStatus.OK)
    check(f"{label} failure still confirms it to the user",
          "ORDER PLACED" in result.message)
    check(f"{label} failure did not stop the order reaching the provider",
          len(spy.placed) == 1)

# And the whole turn, through the planner, with the real ERP schema in place.
phone = "919777000999"
registry.clear()
spy = Spy("spy_grocery", ProviderKind.GROCERY)
registry.register(spy)
identity.load(phone)
show(phone, "spy_grocery", ProviderKind.GROCERY.value,
     [("SPIN-1::SKU-1", "Amul Taaza Milk", "Amul", 33, None)])

original_save = db.save_order
db.save_order = lambda **k: (_ for _ in ()).throw(
    sqlite3.OperationalError("table orders has no column named provider"))


class Echo:
    """Returns the tool result verbatim, so the reply reflects what the skill said."""

    def __init__(self):
        self.seen = []

    async def chat(self, messages, tools=None, temperature=None):
        self.seen.append(messages[-1]["content"])
        return LLMReply(text="Ordered! 🎉")


echo = Echo()
planner.get_llm = lambda: echo
try:
    reply = run(planner.plan(phone, "1"))
finally:
    db.save_order = original_save

check("the turn did NOT raise out of plan()", reply == "Ordered! 🎉")
check("the model was handed a success, not an error",
      any("ORDER PLACED" in s for s in echo.seen))
check("the order really was placed", len(spy.placed) == 1)

# ======================================================================
print("\n[12] A conversation stranded mid-order can recover")
phone = "919777001111"
conversation.reset(phone)
db.save_conversation_state(phone=phone, state=conversation.State.ORDERING,
                           offers=None, query="milk", pending=None)
try:
    conversation.show_offers(phone, [{"provider": "p", "id": "1", "title": "Milk",
                                      "venue": None, "price": 33, "currency": "INR",
                                      "eta_minutes": None, "kind": "grocery"}], "milk")
    check("a new search from ORDERING is allowed, not an IllegalTransition", True)
except conversation.IllegalTransition:
    check("a new search from ORDERING is allowed, not an IllegalTransition", False)
check("and it lands in AWAITING_SELECTION",
      conversation.load(phone).state == conversation.State.AWAITING_SELECTION)

try:
    os.unlink(legacy.name)
except OSError:
    pass

# ======================================================================
print("\n[13] A refused PAYMENT METHOD is never retried")
# Observed live: the cart built perfectly, then Swiggy refused checkout with
# "To minimise contact between you and the delivery partner, cash option is
# temporarily disabled." Asking "shall I try again?" for that is a lie — it
# fails identically every time.
from ai.providers import failures
from ai.providers.failures import Failure

check("Swiggy's exact wording is recognised",
      swiggy_food._is_payment_problem(
          "Failed to place order: To minimise contact between you and the "
          "delivery partner, cash option is temporarily disabled."))
check("a sold-out message is NOT mistaken for a payment problem",
      not swiggy_food._is_payment_problem("Item is Out of Stock"))
check("payment failures are in the no-retry set",
      Failure.PAYMENT_UNAVAILABLE in failures.NO_RETRY)
check("item problems ARE still retryable",
      Failure.ITEM_UNAVAILABLE not in failures.NO_RETRY)

refused = FoodClient(checkout=Result(
    is_error=True,
    text="Failed to place order: cash option is temporarily disabled."))
try:
    run(food_provider(refused).place(offers[0], 1, SearchContext()))
    check("checkout raises on a refused payment method", False)
except ProviderError as e:
    check("checkout raises on a refused payment method", True)
    check("and is classified as PAYMENT_UNAVAILABLE",
          failures.classify(e) is Failure.PAYMENT_UNAVAILABLE)

instruction = failures.INSTRUCTION[Failure.PAYMENT_UNAVAILABLE]
check("the model is told NOT to offer a retry", "Do NOT offer to try again" in instruction)
check("and told nothing was charged", "Nothing was charged" in instruction)
check("and told it is not the user's fault", "nothing to do with" in instruction)

# Through the skills layer: the pending order is dropped, not left armed.
phone = "919177700001"
registry.clear()
spy = Spy("spy_food", ProviderKind.RESTAURANT)


async def _refuse(offer_, quantity, ctx):
    error = ProviderError("payment method refused: cash option is temporarily disabled")
    error.failure = Failure.PAYMENT_UNAVAILABLE
    raise error


spy.place = _refuse
registry.register(spy)
identity.load(phone)
show(phone, "spy_food", ProviderKind.RESTAURANT.value,
     [("IT-1", "Tomatoes Pizza", "La Pino'z", 97, None)])

result = run(skills.place_order(identity.load(phone), 1))
check("the user is told plainly, not asked to retry",
      "try again" not in result.message.lower().replace("do not offer to try again", ""))
state = conversation.load(phone)
check("no pending order is left armed", state.pending is None)
check("state is NOT left awaiting a retry",
      state.state != conversation.State.AWAITING_RETRY_CONFIRMATION)
# ======================================================================
print("\n[14] UPI: the order is NOT placed until it is paid")
# Swiggy creates the order in PENDING_PAYMENT and returns `bridgeUrl`, an opaque
# HTTPS payment page. Treating that as a placed order would promise food nobody
# has paid for.
from ai.providers.base import PENDING_PAYMENT, PlacedOrder

cash = FoodClient(payment_options={"cod": True})
run(food_provider(cash).place(offers[0], 1, SearchContext()))
check("cash is used when the picker still offers it",
      cash.paid_with[0]["method"] == "Cash")

upi_only = FoodClient(
    payment_options={"allMethods": [
        {"paymentMethod": "UPI", "id": "gpay", "displayName": "Google Pay"},
    ]},
    checkout=Result(structured={
        "orderId": "ORD-9", "paasId": "PAAS-9",
        "bridgeUrl": "https://mcp.swiggy.com/pay/opaque-token",
        "pollingIntervalInMs": 2000, "maxTimeToPollForInMs": 60000,
        "cartTotal": 127,
    }),
)
pending = run(food_provider(upi_only).place(offers[0], 1, SearchContext()))

check("UPI is chosen when cash is NOT offered", upi_only.paid_with[0]["method"] == "UPI")
check("the picked app id is passed through", upi_only.paid_with[0]["intent_app"] == "gpay")
check("status is PENDING_PAYMENT, never PLACED", pending.status == PENDING_PAYMENT)
check("needs_payment says so", pending.needs_payment is True)
check("the payment link is carried", pending.payment_url.startswith("https://"))
check("the payment reference is carried", pending.payment_ref == "PAAS-9")
check("the provider's poll cadence is honoured", pending.poll_interval_ms == 2000)
check("and its timeout", pending.poll_timeout_ms == 60000)
check("the total comes from the provider", pending.total == 127)
check("no ETA is invented for an unpaid order", pending.eta_minutes is None)

# ======================================================================
print("\n[15] The link goes to the user; nothing is claimed until it clears")
pay_phone = "919188800001"
registry.clear()


class PayProvider(Spy):
    def __init__(self):
        super().__init__("pay_food", ProviderKind.RESTAURANT)
        self.statuses = ["pending", "success"]
        self.confirmed = []

    async def place(self, offer, quantity, ctx):
        return PlacedOrder(provider=self.name, order_id="ORD-9",
                           status=PENDING_PAYMENT, total=127,
                           payment_url="https://mcp.swiggy.com/pay/tok",
                           payment_ref="PAAS-9", poll_interval_ms=10,
                           poll_timeout_ms=5000)

    async def payment_status(self, order, ctx):
        return self.statuses.pop(0) if self.statuses else "pending"

    async def confirm_payment(self, order, ctx):
        self.confirmed.append(order.order_id)
        return True


payer = PayProvider()
registry.register(payer)
identity.load(pay_phone)
show(pay_phone, "pay_food", ProviderKind.RESTAURANT.value,
     [("R::I", "Tomatoes Pizza", "La Pino'z", 97, None)])

result = run(skills.place_order(identity.load(pay_phone), 1))
# The link goes STRAIGHT to the user. Routing it through the model cost 24s of a
# 60s UPI window when this ran live on a local model.
queued = [row[0] for row in db.get_connection().execute(
    "SELECT body FROM whatsapp_outbound WHERE phone = ?", (pay_phone,)).fetchall()]
check("the payment link is sent to the user immediately",
      any("https://mcp.swiggy.com/pay/tok" in body for body in queued))
check("and never handed to the model, which would only delay it",
      "https://mcp.swiggy.com/pay/tok" not in result.message)
check("so it cannot be mangled, shortened or omitted in the reply",
      "Do NOT repeat it" in result.message)
check("the model is told NOT to claim it is placed",
      "Do NOT say the order is placed" in result.message)
check("and never to ask for a PIN or card details",
      "never ask for a UPI id, PIN or card details" in result.message)
check("conversation moves to AWAITING_PAYMENT",
      conversation.load(pay_phone).state == conversation.State.AWAITING_PAYMENT)
check("the order is recorded PENDING_PAYMENT, not placed",
      db.get_latest_order(pay_phone)["status"] == PENDING_PAYMENT)


async def _settle():
    """Run the watcher the way skills does, and wait for it to finish."""
    from ai import payments
    order = PlacedOrder(provider="pay_food", order_id="ORD-9",
                        status=PENDING_PAYMENT, payment_url="u",
                        payment_ref="PAAS-9", poll_interval_ms=10,
                        poll_timeout_ms=5000)
    payments.watch(pay_phone, payer, order, SearchContext(), "Tomatoes Pizza",
                   db.get_latest_order(pay_phone)["id"])
    for _ in range(100):
        await asyncio.sleep(0.05)
        if not payments._watchers.get(pay_phone):
            return


run(_settle())
check("the watcher confirms the order once paid", payer.confirmed == ["ORD-9"])
check("the order becomes PLACED", db.get_latest_order(pay_phone)["status"] == "PLACED")
# Queued through the SAME outbound path as any reply, so it inherits ordering,
# retries and restart recovery. Read the table directly: the worker may already
# have tried to send it, which leaves it non-PENDING.
sent = db.get_connection().execute(
    "SELECT body FROM whatsapp_outbound WHERE phone = ?", (pay_phone,)).fetchall()
check("the user is told, without being asked",
      any("on its way" in row[0] for row in sent))
check("the follow-up answers no inbound message",
      db.get_connection().execute(
          "SELECT inbound_id FROM whatsapp_outbound WHERE phone = ? AND body LIKE '%on its way%'",
          (pay_phone,)).fetchone()[0] is None)
check("conversation closes as ORDER_COMPLETE",
      conversation.load(pay_phone).state == conversation.State.ORDER_COMPLETE)

# ======================================================================
print("\n[16] Coupons are applied for the user, never invented")
check("the biggest qualifying saving wins",
      swiggy_food._best_coupon(
          [{"code": "A", "saving": 50.0, "minimum": 0},
           {"code": "B", "saving": 120.0, "minimum": 0}], 300)["code"] == "B")
check("a coupon the cart cannot reach is skipped",
      swiggy_food._best_coupon(
          [{"code": "BIG", "saving": 500.0, "minimum": 999},
           {"code": "SMALL", "saving": 40.0, "minimum": 0}], 300)["code"] == "SMALL")
check("nothing qualifying means no coupon",
      swiggy_food._best_coupon([{"code": "X", "saving": 9.0, "minimum": 1000}], 100) is None)
check("no coupons at all is safe", swiggy_food._best_coupon([], 300) is None)
check("equal savings break deterministically, not by luck",
      all(swiggy_food._best_coupon(
          [{"code": "ZZ", "saving": 50.0, "minimum": 0},
           {"code": "AA", "saving": 50.0, "minimum": 0}], 300)["code"] == "AA"
          for _ in range(5)))

parsed = swiggy_food._coupons({"coupons": [
    {"couponCode": "TRYNEW", "maxDiscount": 120, "minCartAmount": 199,
     "description": "120 off above 199"},
    {"description": "an offer with no code"},
]})
check("a coupon without a code is not an offer", len(parsed) == 1)
check("the code is read", parsed[0]["code"] == "TRYNEW")
check("the saving is read", parsed[0]["saving"] == 120)
check("the minimum is read", parsed[0]["minimum"] == 199)
check("an unreadable payload yields nothing", swiggy_food._coupons("nonsense") == [])


class CouponClient(FoodClient):
    """A Food client that offers coupons."""

    def __init__(self, coupons=None, apply_fails=False, **kw):
        super().__init__(**kw)
        self.coupon_payload = coupons if coupons is not None else {
            "coupons": [{"couponCode": "SAVE60", "maxDiscount": 60, "minCartAmount": 0}]
        }
        self.apply_fails = apply_fails
        self.applied = []

    async def coupons(self, address_id):
        return self.coupon_payload

    async def apply_coupon(self, address_id, code):
        self.applied.append(code)
        return Result(is_error=self.apply_fails,
                      text="Coupon not valid" if self.apply_fails else None)


client = CouponClient()
order = run(food_provider(client).place(offers[0], 1, SearchContext()))
check("the best coupon is applied without being asked", client.applied == ["SAVE60"])
check("and the order says which one", "SAVE60" in (order.note or ""))

# A discount is a bonus. Nothing about it may stop an order.
rejected = CouponClient(apply_fails=True)
order = run(food_provider(rejected).place(offers[0], 1, SearchContext()))
check("a rejected coupon does NOT block the order", order.status == "PLACED")
check("and nothing is claimed about a discount", not order.note)

none_offered = CouponClient(coupons={"coupons": []})
order = run(food_provider(none_offered).place(offers[0], 1, SearchContext()))
check("no coupons offered still orders fine", order.status == "PLACED")
check("and claims no saving", not order.note)


class BrokenCoupons(CouponClient):
    async def coupons(self, address_id):
        raise RuntimeError("coupon service down")


order = run(food_provider(BrokenCoupons()).place(offers[0], 1, SearchContext()))
check("a broken coupon service never breaks ordering", order.status == "PLACED")
check("and claims no saving", not order.note)
# ======================================================================
print("\n[17] REGRESSION: the picker lists APPS, not payment methods")
# Captured live 2026-08-16. Each entry's id ("gpay://upi/") is the intentApp;
# the method is always "UPI". Sending the id as the method got:
#   Unsupported payment method "gpay://upi/". Use "UPI" with
#   intentApp/generateUPIQR for UPI payments, or "Cash" for cash on delivery.
LIVE_PICKER = {"allMethods": [
    {"id": "gpay://upi/", "displayName": "Google Pay"},
    {"id": "phonepe://upi/", "displayName": "PhonePe UPI"},
    {"id": "paytmmp://upi/", "displayName": "Paytm UPI"},
    {"displayName": "Pay with QR", "generateUPIQR": True},
]}
methods = swiggy_food._payment_methods(LIVE_PICKER)

check("every entry resolves to a method Swiggy accepts",
      all(m["method"] in ("UPI", "Cash") for m in methods))
check("the app id becomes the intentApp, not the method",
      methods[0]["method"] == "UPI" and methods[0]["intent_app"] == "gpay://upi/")
check("the human label is preserved", methods[0]["label"] == "Google Pay")
check("the QR option asks for a QR, with no intentApp",
      methods[-1]["qr"] is True and methods[-1]["intent_app"] is None)
check("the QR option is still UPI", methods[-1]["method"] == "UPI")

check("cash is picked up from the cod field",
      swiggy_food._payment_methods({"cod": True})[0]["method"] == "Cash")
check("cash wins over UPI when both are offered",
      swiggy_food._payment_methods(
          {"allMethods": [{"id": "gpay://upi/", "displayName": "GPay"}], "cod": True}
      )[-1]["method"] == "Cash")

check("an entry with nothing usable is dropped, not sent and rejected",
      swiggy_food._payment_methods({"allMethods": [{"displayName": "Mystery"}]}) == [])
check("an unreadable payload yields nothing", swiggy_food._payment_methods(None) == [])

# End to end: the live shape must produce a call Swiggy accepts.
live = FoodClient(payment_options=LIVE_PICKER, checkout=Result(structured={
    "orderId": "ORD-L", "paasId": "P-L", "bridgeUrl": "https://pay/x"}))
run(food_provider(live).place(offers[0], 1, SearchContext()))
sent = live.paid_with[0]
check("checkout is told UPI, never an app id", sent["method"] == "UPI")
check("and given the app to open", sent["intent_app"] == "gpay://upi/")

# ======================================================================
print("\n[18] The user picks the coupon, and the cart is built exactly once")
# Discounts are scoped to a built cart, so listing them commits the basket and
# `place` then checks the SAME basket out. That means the cart is built twice for
# one order — the flush is what stops the second build stacking quantities and
# charging someone for two dinners.
lister = CouponClient(coupons={"coupons": [
    {"couponCode": "SAVE60", "maxDiscount": 60, "minCartAmount": 0},
    {"couponCode": "FLAT120", "maxDiscount": 120, "minCartAmount": 0},
    {"couponCode": "TOOBIG", "maxDiscount": 900, "minCartAmount": 5000},
]})
listed = run(food_provider(lister).coupons(offers[0], 1, SearchContext()))
check("coupons are listed best saving first", [c.code for c in listed] == ["FLAT120", "SAVE60"])
check("one the cart cannot reach is never shown", "TOOBIG" not in [c.code for c in listed])
check("listing builds the cart", len(lister.carts) == 1)
check("and flushes it first, so a rebuild cannot stack quantities",
      lister.calls[0] == "flush_cart")
check("listing applies nothing on its own", lister.applied == [])

chosen = CouponClient(coupons=lister.coupon_payload)
run(food_provider(chosen).place(offers[0], 1, SearchContext(), coupon="SAVE60"))
check("their choice is applied, not ours", chosen.applied == ["SAVE60"])

declined = CouponClient(coupons=lister.coupon_payload)
order = run(food_provider(declined).place(offers[0], 1, SearchContext(), coupon=""))
check("declining applies no coupon at all", declined.applied == [])
check("and still places the order", order.status == "PLACED")
check("and claims no saving", not order.note)


class CouponSpy(Spy):
    """An ordering provider that offers discounts."""
    supports_coupons = True

    def __init__(self, *a, coupons=("FLAT120", "SAVE60"), **kw):
        super().__init__(*a, **kw)
        self.offering = list(coupons)
        self.coupon_calls = 0
        self.used = []

    async def coupons(self, offer, quantity, ctx):
        from ai.providers.base import Coupon
        self.coupon_calls += 1
        return [Coupon(code=c, label=f"{c} off") for c in self.offering]

    async def place(self, offer, quantity, ctx, coupon=None):
        self.used.append(coupon)
        return await Spy.place(self, offer, quantity, ctx)


ENTRIES = [("IT-1", "Chicken Biryani", "Meghana Foods", 340, 22),
           ("IT-2", "Mutton Biryani", "Empire Restaurant", 280, 18)]


def coupon_turn(phone, reply=None, **kw):
    """Pick option 1, then optionally answer the coupon question."""
    registry.clear()
    spy = CouponSpy("spy_food", ProviderKind.RESTAURANT, **kw)
    registry.register(spy)
    identity.load(phone)
    show(phone, "spy_food", ProviderKind.RESTAURANT.value, ENTRIES)
    planner.get_llm = lambda: Recorder()
    run(planner.plan(phone, "1"))
    if reply is not None:
        run(planner.plan(phone, reply))
    return spy


spy = coupon_turn("919155500180")
state = conversation.load("919155500180")
check("choosing a dish asks about coupons before spending",
      state.state == conversation.State.AWAITING_COUPON)
check("and nothing is ordered at that point", spy.placed == [])
check("the offered codes are remembered in order",
      [c["code"] for c in state.pending.coupons] == ["FLAT120", "SAVE60"])

spy = coupon_turn("919155500181", "2")
check("'2' picks the second coupon", spy.used == ["SAVE60"])
check("and places the order they chose", spy.placed and spy.placed[0][0] == "IT-1")
check("picking a coupon never re-searches", spy.searches == 0)
check("ends in ORDER_COMPLETE",
      conversation.load("919155500181").state == conversation.State.ORDER_COMPLETE)

check("the code can be typed instead of the number",
      coupon_turn("919155500182", "FLAT120").used == ["FLAT120"])
check("case does not matter", coupon_turn("919155500183", "save60").used == ["SAVE60"])
check("'no' skips the discount and still orders",
      coupon_turn("919155500184", "no thanks").used == [""])
check("'yes' takes the best one, which is the one listed first",
      coupon_turn("919155500185", "yes").used == ["FLAT120"])

# Changing their mind mid-checkout must not strand them holding a basket.
spy = coupon_turn("919155500186", "actually I want pizza instead")
state = conversation.load("919155500186")
check("an unrelated reply drops the basket rather than stranding them",
      state.state != conversation.State.AWAITING_COUPON)
check("and nothing was ordered", spy.placed == [])

# A provider with no coupons must not grow a step that could lose the order.
spy = coupon_turn("919155500187", coupons=())
check("no coupons means straight to ordering", spy.placed and spy.placed[0][0] == "IT-1")
check("and no coupon question", conversation.load("919155500187").state
      == conversation.State.ORDER_COMPLETE)
check("with nothing applied", spy.used == [None])

# A retry retries THIS order — the discount included, not asked for again.
spy = coupon_turn("919155500188", "1", fail_times=1)
run(planner.plan("919155500188", "yes"))
check("a retry reuses the coupon they already chose", spy.used == ["FLAT120", "FLAT120"])
check("and never re-asks", spy.coupon_calls == 1)

# THE money boundary. Building a cart writes to the owner's account, so an
# unauthorised number must not get that far — never mind to checkout.
registry.clear()
spy = CouponSpy("spy_food", ProviderKind.RESTAURANT)
registry.register(spy)
stranger = "919999000111"
identity.load(stranger)
show(stranger, "spy_food", ProviderKind.RESTAURANT.value, ENTRIES)
os.environ["AUTHORIZED_PHONES"] = "919155500180"      # deliberately not them
planner.get_llm = lambda: Recorder()
run(planner.plan(stranger, "1"))
check("an unauthorised number never gets a cart built", spy.coupon_calls == 0)
check("and never reaches checkout", spy.placed == [])

# ======================================================================
print("\n[19] REGRESSION: a cart Swiggy rejected is never checked out")
# Captured live 2026-08-16. update_food_cart answered isError=False and printed a
# tidy summary; get_food_cart said the cart was unusable. We logged that and
# spent a checkout anyway, which died with "Some error while creating the order"
# — then offered a retry that could only fail the same way.
BROKEN_CART = {
    "statusCode": 1, "successful": False, "data": None,
    "errorCodes": ["INVALID_ADDON"], "ctaAction": "clearCart",
    "statusMessage": "Restaurant may have removed the item(s) from their menu.",
}
GOOD_CART = {"statusCode": 0, "statusMessage": "CART_UPDATED_SUCCESSFULLY",
             "data": {"cart_id": 646329498, "result": "success"}}

check("Swiggy's rejection is read as a rejection",
      "removed the item" in (swiggy_food._cart_rejected(BROKEN_CART) or ""))
check("a healthy cart is not", swiggy_food._cart_rejected(GOOD_CART) is None)
check("an unreadable cart is not a verdict either",
      swiggy_food._cart_rejected({}) is None and swiggy_food._cart_rejected(None) is None)
check("errorCodes alone are enough to stop",
      swiggy_food._cart_rejected({"errorCodes": ["INVALID_ADDON"]}) is not None)


class BrokenCartClient(FoodClient):
    """update_food_cart says fine; get_food_cart says the cart is unusable."""

    async def call(self, key, args):
        self.calls.append(key)
        if key == "cart":
            return Result(structured=BROKEN_CART)
        return Result(structured={"items": []})


broken = BrokenCartClient()
failed = None
try:
    run(food_provider(broken).place(offers[0], 1, SearchContext()))
except ItemUnavailable as e:
    failed = e
check("a rejected cart stops before checkout", failed is not None)
check("and nothing was paid for", broken.paid_with == [])
check("and the logs carry Swiggy's own reason, not just isError",
      "removed the item" in str(failed))

# Through skills: the user gets alternatives, not a retry that cannot work.
registry.clear()
spy = CouponSpy("spy_food", ProviderKind.RESTAURANT)


async def _unavailable(offer, quantity, ctx, coupon=None):
    raise ItemUnavailable("cart rejected: INVALID_ADDON")


spy.place = _unavailable
spy.offering = []
registry.register(spy)
sold_out = "919155500190"
identity.load(sold_out)
show(sold_out, "spy_food", ProviderKind.RESTAURANT.value, ENTRIES)
outcome = run(skills.place_order(identity.load(sold_out), 1))
check("the user is told it's unavailable, not that something broke",
      outcome.status == skills.SkillStatus.UNAVAILABLE_ITEM)
check("and pointed at the options already on the table",
      "other options" in outcome.message)

print("\n" + "=" * 70)
print(f"RESULT: {_passed} passed, {_failed} failed")
try:
    os.unlink(_tmp.name)
except OSError:
    pass
sys.exit(1 if _failed else 0)

