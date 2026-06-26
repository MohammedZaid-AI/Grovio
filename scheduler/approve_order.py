import asyncio

from db import get_pending_orders
from mcp.swiggy_mcp import SwiggyInstamart


async def main():

    pending_orders = get_pending_orders()

    if not pending_orders:

        print(
            "No pending orders."
        )

        return

    order = pending_orders[0]

    pending_id = order[0]

    product_name = order[1]

    spin_id = order[2]

    quantity = order[3]

    print("\nPending Order\n")

    print(
        f"Product: {product_name}"
    )

    print(
        f"Quantity: {quantity}"
    )

    confirm = input(
        "\nApprove order? (yes/no): "
    )

    if confirm.lower() != "yes":

        print(
            "\nOrder skipped."
        )

        return

    swiggy = await (
        SwiggyInstamart()
        .initialize()
    )

    address_id = (
        await swiggy.get_address_id()
    )

    await swiggy.clear_cart()

    await swiggy.update_cart(
        address_id,
        [
            {
                "spinId": spin_id,
                "quantity": quantity
            }
        ]
    )

    cart = await swiggy.get_cart()

    print("\nCart Ready:")
    print(cart)

    checkout = input(
        "\nPlace order? (yes/no): "
    )

    if checkout.lower() == "yes":

        print("\nRefreshing cart...")

        latest_cart = await swiggy.get_cart()

        print("\nLATEST CART:")
        print(latest_cart)

        result = await swiggy.checkout(
            address_id
        )

        print("\nCHECKOUT RESULT:")
        print(result)


if __name__ == "__main__":
    asyncio.run(main())