import json

from integrations.swiggy.swiggy_mcp import SwiggyInstamart


class SwiggyService:
    """
    High-level wrapper around Swiggy MCP.

    All agents should communicate only
    through this service.
    """

    def __init__(self):

        self.client = None

    # ------------------------------------
    # Initialize
    # ------------------------------------

    async def initialize(self):

        if self.client is None:

            self.client = await SwiggyInstamart().initialize()

        return self.client

    # ------------------------------------
    # Search Products
    # ------------------------------------

    async def search_products(self, product_name):

        client = await self.initialize()

        return await client.get_product_options(product_name)

    # ------------------------------------
    # Cart
    # ------------------------------------

    async def clear_cart(self):

        client = await self.initialize()

        return await client.clear_cart()

    async def get_cart(self):

        client = await self.initialize()

        return await client.get_cart()

    async def build_cart(self, items):

        client = await self.initialize()

        address_id = await client.get_address_id()

        payload = []

        for item in items:

            payload.append(
                {
                    "spinId": item["spinId"],
                    "quantity": item["quantity"]
                }
            )

        await client.clear_cart()

        await client.update_cart(
            address_id,
            payload
        )

        return await client.get_cart()

    # ------------------------------------
    # Checkout
    # ------------------------------------

    async def checkout(self):

        client = await self.initialize()

        address_id = await client.get_address_id()

        result = await client.checkout(address_id)

        if getattr(result, "isError", False):

            return self._parse_error(result)

        return self._parse_success(result)

    # ------------------------------------
    # Parse Success
    # ------------------------------------

    def _parse_success(self, result):

        try:

            data = json.loads(result.content[0].text)

            return {

                "success": True,

                "order_id": data["data"]["orderId"],

                "status": data["data"]["status"],

                "payment": data["data"]["paymentMethod"],

                "total": data["data"]["cartTotal"],

                "message": data["message"]

            }

        except Exception:

            return {

                "success": False,

                "code": "INVALID_RESPONSE",

                "message": "Unable to parse Swiggy response."

            }

    # ------------------------------------
    # Parse Error
    # ------------------------------------

    def _parse_error(self, result):

        text = ""

        if result.content:

            text = result.content[0].text

        if "Max Per Item Quantity Limit" in text:

            return {

                "success": False,

                "code": "LIMIT_EXCEEDED",

                "message":
                    "The selected quantity exceeds Swiggy's allowed limit."

            }

        if "Out of Stock" in text:

            return {

                "success": False,

                "code": "OUT_OF_STOCK",

                "message":
                    "One or more selected products are out of stock."

            }

        return {

            "success": False,

            "code": "UNKNOWN",

            "message": text

        }