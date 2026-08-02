"""
Dump a real Swiggy Food response so the adapter can be mapped to actual fields.

Tool NAMES are verified; the response SHAPE is not. This calls the live server
and prints one raw entry plus the fields the adapter currently looks for, so any
mismatch is obvious.

Read-only — searches and reads addresses. It never carts and never orders.

    python scripts/probe_food.py
    python scripts/probe_food.py "paneer tikka"
"""
import asyncio
import json
import os
import sys

# Run from anywhere, in any shell, without setting PYTHONPATH.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv

load_dotenv()

from integrations.swiggy import swiggy_food_mcp as mcp


def show(label, value):
    ok = "✅" if value not in (None, "", [], {}) else "❌"
    print(f"   {ok} {label:16} {value!r}")


async def main():
    query = sys.argv[1] if len(sys.argv) > 1 else "biryani"

    print(f"\nConnecting to {mcp.SERVER_URL}")
    client = await mcp.SwiggyFood().initialize()
    print(f"✅ {len(client.available)} tools available\n")

    print("1. get_addresses")
    address_id = await client.default_address_id()
    print(f"   addressId = {address_id}\n")

    print(f"2. search_menu({query!r})")
    payload = await client.search_dishes(query, address_id)

    entries = mcp.items_of(payload, "items", "dishes", "results", "menuItems", "cards")
    print(f"   adapter found {len(entries)} entries under its expected keys")

    if not entries:
        print("\n   ⚠️  No entries under items/dishes/results/menuItems/cards.")
        print("   Top-level keys in the response:")
        print(f"     {list(payload.keys())[:25]}")
        print("\n   RAW (first 3000 chars) — paste this:")
        print(json.dumps(payload, indent=2, default=str)[:3000])
        return

    entry = entries[0]
    print("\n3. Field mapping against the first entry")
    show("title", entry.get("name") or entry.get("displayName") or entry.get("itemName"))
    show("restaurantId", entry.get("restaurantId") or entry.get("resId"))
    show("itemId", entry.get("itemId") or entry.get("menuItemId") or entry.get("id"))
    show("venue", entry.get("restaurantName") or entry.get("restaurant"))
    show("price", entry.get("price") or entry.get("finalPrice") or entry.get("defaultPrice"))
    show("rating", entry.get("rating") or entry.get("avgRating"))
    show("eta", entry.get("etaMinutes") or entry.get("sla") or entry.get("deliveryTime"))

    print("\n4. RAW first entry — paste this if anything above shows ❌")
    print(json.dumps(entry, indent=2, default=str)[:2500])

    print("\n5. What the adapter would produce")
    from ai.providers.base import SearchContext
    from ai.providers.swiggy_food import SwiggyFoodProvider

    provider = SwiggyFoodProvider()
    provider._client = client
    offers = await provider.search(query, SearchContext(limit=5))
    if not offers:
        print("   ⚠️  zero offers — field names need correcting (see §4)")
    for offer in offers:
        print(f"   • {offer.summary()}")
        print(f"     id={offer.id}")
    print()


asyncio.run(main())
