"""
LIVE check of the redesigned semantic product selection.

Run in an environment with network + Swiggy MCP auth + LLM keys:

    python scripts/live_product_selection.py

It searches Swiggy for a term (default "coke"), shows the candidates, then runs
the SINGLE semantic decision engine and prints the decision. Use it to confirm
the model resolves "coke" -> Coca-Cola without any alias table, and that memory
acts as a signal rather than a veto.
"""
import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from ai.services.swiggy_service import SwiggyService
from ai.agents.product_selection_agent import product_selector
from ai.memory.restaurant_memory import restaurant_memory


async def main():
    query = (input("Search term (default 'coke'): ").strip() or "coke")

    svc = SwiggyService()
    products = await svc.search_products(query)
    if not products:
        print(f"No products found for {query!r}")
        return

    print(f"\nCandidates for {query!r}:")
    for i, p in enumerate(products[:8]):
        v = p["variations"][0]
        print(f"  [{i}] {p['displayName']}  ({v.get('quantityDescription')})  ₹{v['price']['offerPrice']}")

    print("\nMemory summary fed to the ranker:")
    print(restaurant_memory.summary())

    decision = await product_selector.execute(query, products)

    print("\n=== DECISION ===")
    print(decision)
    if decision.get("action") == "auto_select":
        chosen = products[decision["index"]]
        print(f"\nAuto-selected: {chosen['displayName']}  "
              f"(category={decision.get('category')}, confidence={decision.get('confidence')})")
    else:
        print(f"\nAsking the user (reason: {decision.get('reason')})")


if __name__ == "__main__":
    asyncio.run(main())
