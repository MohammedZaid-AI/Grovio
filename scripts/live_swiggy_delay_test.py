"""
LIVE proof that the PERSISTENT shopping session survives a real reply delay.

Run in an environment with network + Swiggy MCP auth:

    python scripts/live_swiggy_delay_test.py

Increment 2 behaviour: ONE SwiggyService (one MCP session) is reused for the
whole shopping conversation. We:
  1. build the cart,
  2. sleep 20 seconds (simulating the user going away),
  3. get_cart() to confirm it is still alive,
  4. checkout with Cash on Delivery — WITHOUT rebuilding.

If checkout succeeds without a rebuild, the persistent session held the cart
across the delay (success criteria met). If Swiggy expired the server-side
session, the app's revalidation would rebuild once — this script prints which
path happened.
"""
import asyncio
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from ai.services.swiggy_service import SwiggyService

DELAY_SECONDS = 20


async def main():
    item_name = input("Product to order (e.g. Milk): ").strip() or "Milk"

    # ONE persistent service for the whole conversation.
    svc = SwiggyService()

    products = await svc.search_products(item_name)
    if not products:
        print(f"No products found for {item_name}")
        return

    variant = products[0]["variations"][0]
    selected = [{
        "displayName": products[0]["displayName"],
        "spinId": variant["spinId"],
        "quantity": 1,
        "price": variant["price"]["offerPrice"],
    }]
    print(f"\nBuilding cart on the persistent session: {selected[0]['displayName']} x1")
    await svc.build_cart(selected)

    print(f"\nSleeping {DELAY_SECONDS}s (user goes away)...")
    time.sleep(DELAY_SECONDS)

    print("\nRevalidating cart on the SAME session (get_cart)...")
    try:
        cart = await svc.get_cart()
        print("get_cart result:", cart)
    except Exception as e:
        print("get_cart raised:", e)

    print("\nChecking out with Cash on Delivery — NO rebuild...")
    result = await svc.checkout(payment_method="Cash")
    print("\n=== CHECKOUT RESULT ===")
    print(result)

    if result.get("order_placed") or result.get("success"):
        print(f"\n✅ Persistent session survived the {DELAY_SECONDS}s delay — order placed "
              f"without a rebuild. Order ID: {result.get('order_id')}")
    else:
        print(f"\n⚠️ Checkout did not succeed directly. Message: {result.get('message')}")
        if result.get("raw_error"):
            print(f"   (raw error, logs only): {result['raw_error']}")
        print("   In the live app, revalidation would rebuild once and retry here.")


if __name__ == "__main__":
    asyncio.run(main())
