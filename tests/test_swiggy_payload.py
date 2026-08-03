"""
Regression tests for the Swiggy payload contract.

These pin three bugs that broke ordering during the pivot. All three were
present in `ai/services/swiggy_service.py` BEFORE Phase 2 deleted it, and were
reintroduced when Phase 5 rewrote `place()` from the raw MCP client.

  1. price lives at variations[0].price.offerPrice — an OBJECT, not a number
  2. update_cart needs skuId ALONGSIDE spinId, or it adds nothing and reports
     success; the empty cart then fails at checkout
  3. the cart is cleared before adding, so a stale line can't poison checkout

No network. A fake MCP client records exactly what the adapter would send.
"""
import asyncio
import os
import sys
import tempfile

sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import db

_tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
_tmp.close()
db.DB_PATH = _tmp.name
db.init_db()

from ai.providers import swiggy
from ai.providers.base import ItemUnavailable, SearchContext

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
    """Minimal stand-in for an MCP tool result."""

    def __init__(self, is_error=False, payload=None):
        self.isError = is_error
        self.structuredContent = payload or {}
        self.content = None


class FakeClient:
    """Records every call the adapter makes, so the payload can be asserted."""

    def __init__(self, cart_after_add=None, checkout_error=False):
        self.calls = []
        self.cart_items = cart_after_add if cart_after_add is not None else [{"x": 1}]
        self.checkout_error = checkout_error

    async def get_address_id(self):
        self.calls.append(("get_address_id", None))
        return "addr-1"

    async def clear_cart(self):
        self.calls.append(("clear_cart", None))
        return Result()

    async def update_cart(self, address_id, items):
        self.calls.append(("update_cart", items))
        return Result()

    async def get_cart(self):
        self.calls.append(("get_cart", None))
        return Result(payload={"items": self.cart_items})

    async def checkout(self, address_id, payment_method=None):
        self.calls.append(("checkout", payment_method))
        if self.checkout_error:
            return Result(is_error=True)
        return Result(payload={"orderId": "OID-1", "total": 249, "etaMinutes": 12})

    async def get_product_options(self, query):
        return PRODUCTS


# The real Instamart shape: everything orderable lives on the variant, and
# price is a nested object.
PRODUCTS = [
    {
        "name": "Daawat Biryani Kit Hyderabadi",
        "brand": "Daawat",
        "skuId": "SKU-TOP",
        "variations": [{
            "spinId": "SPIN-1",
            "skuId": "SKU-1",
            "price": {"offerPrice": 249, "mrp": 299, "storePrice": 275},
            "quantity": "500 g",
            "inStock": True,
        }],
    },
    {
        "name": "Cookd Ambur Biryani Kit",
        "brand": "Cookd",
        "variations": [{
            "spinId": "SPIN-2",
            "price": {"offerPrice": 199},
            "inStock": True,
        }],
    },
    # Not orderable: no spinId anywhere.
    {"name": "Ghost Product", "brand": "Nobody", "variations": [{"price": {"offerPrice": 10}}]},
]


def provider_with(client):
    provider = swiggy.SwiggyInstamartProvider()
    provider._client = client
    return provider


# ----------------------------------------------------------------------
print("\n[1] REGRESSION: price is nested at variations[0].price.offerPrice")
check("nested offerPrice is read", swiggy._price_of(PRODUCTS[0]) == 249)
check("prefers offerPrice over mrp", swiggy._price_of(PRODUCTS[0]) != 299)
check("flat price still works", swiggy._price_of({"price": 99}) == 99)
check("price object without offerPrice falls back",
      swiggy._price_of({"variations": [{"price": {"storePrice": 150}}]}) == 150)
check("missing price stays None, never 0",
      swiggy._price_of({"variations": [{"spinId": "x"}]}) is None)

offers = run(provider_with(FakeClient()).search("biryani", SearchContext(limit=10)))
check("every offer carries a real price", all(o.price is not None for o in offers))
check("first offer priced correctly", offers[0].price == 249)
check("price reaches the user-facing summary", "249" in offers[0].summary())

# ----------------------------------------------------------------------
print("\n[2] REGRESSION: cart needs skuId ALONGSIDE spinId")
spin, sku = swiggy._unpack_id(offers[0].id)
check("offer id carries the spinId", spin == "SPIN-1")
check("offer id carries the skuId", sku == "SKU-1")

client = FakeClient()
run(provider_with(client).place(offers[0], 1, SearchContext()))
sent = next(items for name, items in client.calls if name == "update_cart")

check("cart item includes spinId", sent[0].get("spinId") == "SPIN-1")
check("cart item includes skuId (the bug that broke checkout)", sent[0].get("skuId") == "SKU-1")
check("cart item includes quantity", sent[0].get("quantity") == 1)

# A product whose skuId is only at the top level must still supply one.
top_level = {"name": "T", "skuId": "SKU-TOP2",
             "variations": [{"spinId": "SPIN-9", "price": {"offerPrice": 10}}]}
offer = run(provider_with(FakeClient()).search("x", SearchContext()))
check("variant skuId WINS over the product-level one (identifies the pack)",
      swiggy._pick_variant_first(PRODUCTS[0], "skuId") == "SKU-1")
check("skuId falls back to the product level when the variant has none",
      swiggy._pick_variant_first(top_level, "skuId") == "SKU-TOP2")

# ----------------------------------------------------------------------
print("\n[3] REGRESSION: the cart is cleared before adding")
client = FakeClient()
run(provider_with(client).place(offers[0], 1, SearchContext()))
order = [name for name, _ in client.calls]
check("clear_cart happens", "clear_cart" in order)
check("clear_cart happens BEFORE update_cart",
      order.index("clear_cart") < order.index("update_cart"))
check("cart is verified after adding", "get_cart" in order)
check("checkout happens last", order.index("checkout") > order.index("update_cart"))
check("checkout is cash on delivery", ("checkout", "Cash") in client.calls)

# ----------------------------------------------------------------------
print("\n[4] An empty cart is caught at the add, not at checkout")
client = FakeClient(cart_after_add=[])
try:
    run(provider_with(client).place(offers[0], 1, SearchContext()))
    check("empty cart after add raises ItemUnavailable", False)
except ItemUnavailable:
    check("empty cart after add raises ItemUnavailable", True)
check("checkout is never attempted on an empty cart",
      "checkout" not in [name for name, _ in client.calls])

# ----------------------------------------------------------------------
print("\n[5] Unorderable products are never offered")
titles = [o.title for o in offers]
check("product without a spinId is excluded", "Ghost Product" not in titles)
check("both orderable products offered", len(offers) == 2)

# ----------------------------------------------------------------------
print("\n[6] A successful order returns real values only")
client = FakeClient()
placed = run(provider_with(client).place(offers[0], 2, SearchContext()))
check("order id from the provider", placed.order_id == "OID-1")
check("total from the provider", placed.total == 249)
check("eta from the provider", placed.eta_minutes == 12)
check("quantity is honoured",
      next(i for n, i in client.calls if n == "update_cart")[0]["quantity"] == 2)

client = FakeClient(checkout_error=True)
try:
    run(provider_with(client).place(offers[0], 1, SearchContext()))
    check("checkout failure raises", False)
except Exception as e:
    check("checkout failure raises", "checkout" in str(e).lower())

print("\n" + "=" * 70)
print(f"RESULT: {_passed} passed, {_failed} failed")
try:
    os.unlink(_tmp.name)
except OSError:
    pass
sys.exit(1 if _failed else 0)
