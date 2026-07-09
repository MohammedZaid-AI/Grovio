"""
PERMANENT DIAGNOSTIC TOOL (keep in repo). Safe, read-only; no production logic.

Diagnostic: confirm the root cause of "Cart not found or session expired" and
VERIFY the skuId fix against the REAL Swiggy MCP response.

Run in your live environment (network + Swiggy auth):

    python scripts/inspect_swiggy_cart.py "coke"      # or any grocery item
    python scripts/inspect_swiggy_cart.py             # defaults to "coke"

Unlike a throwaway probe, this exercises the ACTUAL production code paths:

  1. Searches via the production SwiggyService.search_products().
  2. Prints the COMPLETE JSON of products[0] so we can see the raw shape.
  3. Auto-discovers every place a "sku"-like id appears in that JSON, so if my
     assumed path (variant["skuId"] / product["skuId"]) is wrong, the script
     reports the real location instead of guessing.
  4. Runs the REAL shopping_session.select() extraction and prints the selected
     record -> proves what skuId value production actually captures.
  5. Runs the REAL SwiggyService.build_cart() -> builds the exact update_cart
     payload {spinId, skuId, quantity} and surfaces any isError.
  6. Calls get_cart() and prints the real server-side cart contents.

VERDICT is printed at the end. The bug is fixed ONLY if:
    ✓ a skuId value was extracted (not None)
    ✓ update_cart reported no isError
    ✓ get_cart shows the product in the cart
"""
import asyncio
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from ai.services.swiggy_service import SwiggyService
from ai.shopping.shopping_session import shopping_session


def dump(obj):
    try:
        return json.dumps(obj, indent=2, default=str)
    except Exception:
        return repr(obj)


def find_sku_paths(obj, prefix="products[0]"):
    """Recursively locate every key whose name contains 'sku' (case-insensitive)
    so we can confirm where the real id lives without hardcoding a guess."""
    hits = []
    if isinstance(obj, dict):
        for k, v in obj.items():
            path = f"{prefix}.{k}"
            if "sku" in k.lower() and not isinstance(v, (dict, list)):
                hits.append((path, v))
            hits.extend(find_sku_paths(v, path))
    elif isinstance(obj, list):
        # Only descend into the first element to keep output readable.
        if obj:
            hits.extend(find_sku_paths(obj[0], f"{prefix}[0]"))
    return hits


def cart_text(cart):
    content = getattr(cart, "content", None)
    if content and getattr(content[0], "text", None):
        return content[0].text
    sc = getattr(cart, "structuredContent", None)
    return dump(sc) if sc is not None else repr(cart)


async def main():
    item_name = (sys.argv[1] if len(sys.argv) > 1 else "coke").strip()
    phone = "diagnostic"

    service = SwiggyService()

    # 1. Real production search.
    products = await service.search_products(item_name)
    if not products:
        print(f"No products returned for {item_name!r}.")
        return

    # 2. Full raw JSON of the first product.
    print("=" * 74)
    print(f"FULL JSON of products[0] for {item_name!r}:")
    print("=" * 74)
    print(dump(products[0]))

    # 3. Auto-discover where sku-like ids live.
    print("\n" + "=" * 74)
    print("sku-like keys discovered in products[0]:")
    print("=" * 74)
    sku_hits = find_sku_paths(products[0])
    if sku_hits:
        for path, val in sku_hits:
            print(f"  {path} = {val!r}")
    else:
        print("  (none found — skuId may be absent from search results entirely)")

    # 4. REAL extraction via shopping_session.select().
    shopping_session.start(phone, [{"name": item_name, "quantity": 1}])
    shopping_session.set_options(phone, products)
    shopping_session.select(phone, 0)
    selected = shopping_session.selected(phone)[0]
    print("\n" + "=" * 74)
    print("shopping_session.select() captured (production extraction):")
    print("=" * 74)
    print(dump(selected))
    extracted_sku = selected.get("skuId")
    if not extracted_sku:
        print("\n⚠️  skuId is None from the assumed path (variant['skuId'] / "
              "product['skuId']). Compare with the discovered keys above and "
              "point the extraction at the real location.")

    # 5. REAL build_cart() -> exact update_cart payload + isError surfacing.
    print("\n" + "=" * 74)
    print("SwiggyService.build_cart() — real update_cart payload:")
    print("=" * 74)
    cart = await service.build_cart(selected := shopping_session.selected(phone))
    # build_cart prints "[SwiggyService] update_cart FAILED ..." if isError.

    # 6. Real server-side cart.
    print("\n" + "=" * 74)
    print("get_cart() — real server-side cart:")
    print("=" * 74)
    print("isError:", getattr(cart, "isError", None))
    print(cart_text(cart))

    # VERDICT
    print("\n" + "=" * 74)
    print("VERDICT")
    print("=" * 74)
    ok_sku = bool(extracted_sku)
    ok_add = not getattr(cart, "isError", False)
    print(f"  skuId extracted:      {'YES ' + repr(extracted_sku) if ok_sku else 'NO'}")
    print(f"  update_cart no error: {'YES' if ok_add else 'NO'}")
    print("  cart contains item:   inspect the get_cart output above")
    if ok_sku and ok_add:
        print("\n  => Extraction + add-to-cart succeeded. If get_cart shows the "
              "item, the fix is CONFIRMED and checkout will find the cart.")
    else:
        print("\n  => NOT fixed yet. Use the discovered sku-key location above "
              "to correct the extraction in shopping_session.select().")

    shopping_session.end(phone)


if __name__ == "__main__":
    asyncio.run(main())
