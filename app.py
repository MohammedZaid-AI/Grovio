import asyncio

from agent import parse_order
from swiggy_mcp import SwiggyInstamart


async def main():

    user_text = input(
        "\nWhat would you like to order?\n\n"
    )

    parsed = parse_order(
        user_text
    )

    print("\nParsed Order:")
    print(parsed)

    items = parsed.get(
        "items",
        []
    )

    if not items:
        print(
            "No items found."
        )
        return

    swiggy = await (
        SwiggyInstamart()
        .initialize()
    )

    await swiggy.place_multiple_items(
        items
    )

    choice = input(
        "\nPlace order? (yes/no): "
    ).strip().lower()

    if choice != "yes":
        print(
            "\nOrder cancelled."
        )
        return

    address_id = (
        await swiggy.get_address_id()
    )

    result = await swiggy.checkout(
        address_id
    )

    print("\nRESULT:")
    print(result)


if __name__ == "__main__":
    asyncio.run(main())