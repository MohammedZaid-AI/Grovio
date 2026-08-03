"""
Dump a real Instamart product so the adapter maps to actual fields.

Read-only: it searches and reads the cart. It never adds and never orders.

    python scripts/probe_instamart.py
    python scripts/probe_instamart.py "biryani kit"
"""
import asyncio
import json
import os
import sys

# Run from anywhere, in any shell, without setting PYTHONPATH.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv

load_dotenv()

from ai.providers.swiggy import _pick, _to_float, _variant
from integrations.swiggy.swiggy_mcp import SwiggyInstamart


def show(label, value):
    print(f"   {'✅' if value not in (None, '', [], {}) else '❌'} {label:12} {value!r}")


async def main():
    query = sys.argv[1] if len(sys.argv) > 1 else "biryani"

    print(f"\nConnecting to Instamart…")
    swiggy = await SwiggyInstamart().initialize()

    print(f"\n1. search {query!r}")
    products = await swiggy.get_product_options(query)
    print(f"   {len(products)} products returned")
    if not products:
        return

    product = products[0]
    print(f"\n2. First product — top-level keys")
    print(f"   {list(product.keys())}")

    variant = _variant(product)
    if variant:
        print(f"\n   has variations[0] with keys:")
        print(f"   {list(variant.keys())}")
    else:
        print("\n   (no variations array — fields are flat)")

    print("\n3. What the adapter extracts")
    show("title", _pick(product, "name", "displayName", "productName"))
    show("spinId", _pick(product, "spinId", "spin_id"))
    show("price", _to_float(_pick(product, "offerPrice", "price", "storePrice", "mrp")))
    show("brand", _pick(product, "brand", "storeName"))
    show("inStock", _pick(product, "inStock", "available"))
    show("pack", _pick(product, "quantity", "packSize", "weight"))

    print("\n4. RAW first product — paste this if anything above is ❌")
    print(json.dumps(product, indent=2, default=str)[:2500])

    print("\n5. What the provider would offer")
    from ai.providers.base import SearchContext
    from ai.providers.swiggy import SwiggyInstamartProvider

    provider = SwiggyInstamartProvider()
    provider._client = swiggy
    offers = await provider.search(query, SearchContext(limit=5))
    if not offers:
        print("   ⚠️  zero orderable offers — no spinId found (see §4)")
    for offer in offers:
        print(f"   • {offer.summary()}   [spinId={offer.id}]")
    print()


asyncio.run(main())
