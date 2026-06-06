import asyncio

from agent import parse_order
from swiggy_mcp import SwiggyInstamart


from db import (
    save_order,
    get_orders,
    get_pending_orders
)

from scheduler import run_scheduler


async def order_now():

    swiggy = await (
        SwiggyInstamart()
        .initialize()
    )

    user_text = input(
        "\nWhat would you like to order?\n\n"
    )

    parsed = parse_order(
        user_text
    )

    print("\nParsed:")
    print(parsed)

    final_items = []

    for item in parsed["items"]:

        products = await swiggy.get_product_options(
            item["name"]
        )

        if not products:

            print(
                f"\nNo results found for {item['name']}"
            )

            continue

        print(
            f"\nResults for {item['name']}:"
        )

        for i, product in enumerate(
            products[:5],
            start=1
        ):

            variant = product[
                "variations"
            ][0]

            print(
                f"{i}. "
                f"{product['displayName']} "
                f"({variant['quantityDescription']}) "
                f"₹{variant['price']['offerPrice']}"
            )

        choice = int(
            input(
                "\nChoose product number: "
            )
        )

        selected = products[
            choice - 1
        ]

        variant = selected[
            "variations"
        ][0]

        final_items.append(
            {
                "spinId":
                    variant["spinId"],
                "quantity":
                    item["quantity"]
            }
        )

    print(
        "\nFINAL ITEMS:"
    )

    print(
        final_items
    )

    await swiggy.clear_cart()

    address_id = await (
        swiggy.get_address_id()
    )

    await swiggy.update_cart(
    address_id,
    final_items
    )

    cart = await swiggy.get_cart()

    print(
        "\nCART:"
    )

    print(cart)

    confirm = input(
        "\nCheckout? (yes/no): "
    )

    if confirm.lower() == "yes":

        latest_cart = await (
            swiggy.get_cart()
        )

        print(
            "\nLATEST CART:"
        )

        print(
            latest_cart
        )

        result = await (
            swiggy.checkout(
                address_id
            )
        )

        print(
            "\nCHECKOUT RESULT:"
        )

        print(
            result
        )


async def create_recurring():

    swiggy = await (
        SwiggyInstamart()
        .initialize()
    )

    product_name = input(
        "\nProduct Name: "
    )

    quantity = int(
        input(
            "Quantity: "
        )
    )

    recurrence = input(
        "Days (Monday,Friday): "
    )

    schedule_time = input(
        "Time (08:00): "
    )

    products = await swiggy.get_product_options(
        product_name
    )

    if not products:

        print(
            "\nNo products found."
        )

        return

    print(
        f"\nResults for {product_name}:"
    )

    for i, product in enumerate(
        products[:5],
        start=1
    ):

        variant = product[
            "variations"
        ][0]

        print(
            f"{i}. "
            f"{product['displayName']} "
            f"({variant['quantityDescription']}) "
            f"₹{variant['price']['offerPrice']}"
        )

    choice = int(
        input(
            "\nChoose product number: "
        )
    )

    selected = products[
        choice - 1
    ]

    variant = selected[
        "variations"
    ][0]

    spin_id = variant[
        "spinId"
    ]

    product_display_name = selected[
        "displayName"
    ]

    save_order(
        product_name=product_display_name,
        spin_id=spin_id,
        quantity=quantity,
        order_type="recurring",
        schedule_time=schedule_time,
        recurrence=recurrence
    )

    print(
        "\nRecurring order saved successfully."
    )

    print(
        f"\nProduct: {product_display_name}"
    )

    print(
        f"Spin ID: {spin_id}"
    )


def view_orders_menu():

    orders = get_orders()

    print("\nOrders:\n")

    for order in orders:
        print(order)


def view_pending():

    orders = get_pending_orders()

    print("\nPending Orders:\n")

    for order in orders:
        print(order)


async def main():

    while True:

        print(
            """
=== GROVIO ===

1. Order groceries now
2. Create recurring order
3. View recurring orders
4. Run scheduler
5. View pending orders
6. Exit
"""
        )

        choice = input(
            "Select option: "
        )

        if choice == "1":

            await order_now()

        elif choice == "2":

            await create_recurring()

        elif choice == "3":

            view_orders_menu()

        elif choice == "4":

            run_scheduler()

        elif choice == "5":

            view_pending()

        elif choice == "6":

            break


if __name__ == "__main__":
    asyncio.run(main())