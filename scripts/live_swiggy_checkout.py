"""
LIVE end-to-end Swiggy checkout test — payment-selection flow.

Run this in an environment that has network access AND valid Swiggy MCP auth
(i.e. the same environment where "Order groceries" works over WhatsApp).
It is intentionally NOT part of the mocked CI suite, because it places a
real cart and drives the real Swiggy MCP.

    python scripts/live_swiggy_checkout.py

Flow proven:
    1. build cart
    2. get_payment_options  (NEW required step)
    3. user selects a payment method
    4. checkout WITH the selected paymentMethod
"""
import asyncio
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from ai.services.swiggy_service import SwiggyService


async def main():
    service = SwiggyService()

    # 1. Build a small real cart. Replace spinId with a real one from a
    #    search_products call, or reuse an item you know is in stock.
    item_name = input("Product to order (e.g. Milk): ").strip() or "Milk"
    products = await service.search_products(item_name)
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
    print(f"\nUsing: {selected[0]['displayName']} x1  @ ₹{selected[0]['price']}")

    await service.build_cart(selected)
    print("Cart built.")

    # 2. get_payment_options (the previously-missing step)
    payment = await service.get_payment_options()
    print("\nget_payment_options ->", payment)

    if payment.get("success") and payment.get("options"):
        options = payment["options"]
        print("\nChoose payment method:")
        for i, opt in enumerate(options, 1):
            print(f"  {i}. {opt['label']}  "
                  f"(paymentMethod={opt['paymentMethod']}, intentApp={opt.get('intentApp')})")
        choice = int(input("\nSelect number: ").strip())
        method = options[choice - 1]
        print(f"Selected: {method['label']}")

        # 4. checkout WITH the selected method (UPI needs intentApp)
        result = await service.checkout(
            payment_method=method["paymentMethod"],
            intent_app=method.get("intentApp"),
            generate_upi_qr=method.get("generateUPIQR", False),
        )
    else:
        # Fallback: options unavailable (Swiggy whitelist) -> Cash on Delivery.
        print("\nPayment options unavailable — falling back to Cash on Delivery.")
        confirm = input("Place order with Cash on Delivery? (yes/no): ").strip().lower()
        if confirm != "yes":
            print("Cancelled.")
            return
        result = await service.checkout(payment_method="Cash")
    print("\n=== CHECKOUT RESULT ===")
    print(result)

    if result.get("order_placed") or result.get("success"):
        print(f"\n✅ Order placed. ID: {result.get('order_id')}  Total: ₹{result.get('total')}  "
              f"Payment: {result.get('payment')}")
    else:
        print(f"\n❌ Checkout not completed. User-facing message: {result.get('message')}")
        if result.get("raw_error"):
            print(f"   (server-side raw error, not shown to users): {result['raw_error']}")


if __name__ == "__main__":
    asyncio.run(main())
