import asyncio
from mcp_use import MCPClient


class SwiggyInstamart:

    def __init__(self):
        self.client = MCPClient.from_config_file("mcp.json")
        self.session = None

    async def initialize(self):
        sessions = await self.client.create_all_sessions()
        self.session = sessions["instamart"]
        return self

    async def get_default_address(self):
        return await self.session.call_tool(
            "get_addresses",
            {}
        )

    async def get_address_id(self):

        addresses = await self.get_default_address()

        address_list = addresses.structuredContent.get(
            "addresses",
            []
        )

        if not address_list:
            raise Exception(
                "No saved addresses found"
            )

        return address_list[0]["id"]

    async def search_product(
        self,
        address_id,
        query
    ):

        return await self.session.call_tool(
            "search_products",
            {
                "addressId": address_id,
                "query": query
            }
        )

    async def clear_cart(self):

        return await self.session.call_tool(
            "clear_cart",
            {}
        )

    async def get_cart(self):

        return await self.session.call_tool(
            "get_cart",
            {}
        )

    async def update_cart(
        self,
        address_id,
        spin_id,
        quantity
    ):

        return await self.session.call_tool(
            "update_cart",
            {
                "selectedAddressId": address_id,
                "items": [
                    {
                        "spinId": spin_id,
                        "quantity": quantity
                    }
                ]
            }
        )

    async def find_product(
        self,
        item_name
    ):

        address_id = await self.get_address_id()

        search_result = await self.search_product(
            address_id,
            item_name
        )

        print("\nSEARCH RESULT:")
        print(search_result)

        if getattr(
            search_result,
            "isError",
            False
        ):
            return None

        return getattr(
            search_result,
            "structuredContent",
            {}
        )

    async def place_item(
        self,
        item_name,
        quantity
    ):

        address_id = await self.get_address_id()

        data = await self.find_product(
            item_name
        )

        if not data:
            print(
                f"Could not find product: {item_name}"
            )
            return None

        products = data.get(
            "products",
            []
        )

        if not products:
            print(
                f"No products found for {item_name}"
            )
            return None

        product = products[0]

        print("\nFIRST PRODUCT:")
        print(product)

        # Try common fields
        spin_id = None

        if "spinId" in product:
            spin_id = product["spinId"]

        elif (
            "variations" in product
            and product["variations"]
        ):
            spin_id = (
                product["variations"][0]
                .get("spinId")
            )

        if not spin_id:

            print(
                "\nCould not find spinId."
            )

            print(product)

            return None

        product_name = (
            product.get("displayName")
            or product.get("name")
            or "Unknown Product"
        )

        print(
            f"\nSelected Product: {product_name}"
        )

        print(
            f"Using Spin ID: {spin_id}"
        )

        result = await self.update_cart(
            address_id,
            spin_id,
            quantity
        )

        print(
            "\nUPDATE CART RESULT:"
        )

        print(result)

        return result

    async def place_multiple_items(
        self,
        items
    ):

        await self.clear_cart()

        for item in items:

            await self.place_item(
                item["name"],
                item["quantity"]
            )

        cart = await self.get_cart()

        print("\nCURRENT CART:")
        print(cart)

        return cart

    async def checkout(
        self,
        address_id,
        payment_method="COD"
    ):

        return await self.session.call_tool(
            "checkout",
            {
                "selectedAddressId": address_id,
                "paymentMethod": payment_method
            }
        )


async def main():

    swiggy = await (
        SwiggyInstamart()
        .initialize()
    )

    item = input(
        "\nWhat do you want to order? "
    )

    quantity = int(
        input("Quantity: ")
    )

    order = [
        {
            "name": item,
            "quantity": quantity
        }
    ]

    cart = await swiggy.place_multiple_items(
        order
    )

    print("\nFINAL CART:")
    print(cart)

    choice = input(
        "\nCheckout? (yes/no): "
    ).strip().lower()

    if choice == "yes":

        address_id = (
            await swiggy.get_address_id()
        )

        result = await swiggy.checkout(
            address_id
        )

        print(
            "\nCHECKOUT RESULT:"
        )

        print(result)

    else:

        print(
            "\nCheckout skipped."
        )


if __name__ == "__main__":
    asyncio.run(main())