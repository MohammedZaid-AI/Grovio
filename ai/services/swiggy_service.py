import json

from integrations.swiggy.swiggy_mcp import SwiggyInstamart
from ai.agents.checkout_recovery_agent import checkout_recovery


class SwiggyService:
    """
    High-level wrapper around Swiggy MCP.

    All agents communicate with Swiggy
    only through this service.
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

    async def search_products(

        self,

        product_name

    ):

        client = await self.initialize()

        return await client.get_product_options(

            product_name

        )

    # ------------------------------------
    # Cart
    # ------------------------------------

    async def clear_cart(self):

        client = await self.initialize()

        return await client.clear_cart()

    async def get_cart(self):

        client = await self.initialize()

        return await client.get_cart()

    async def build_cart(

        self,

        items

    ):

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

        result = await client.checkout(

            address_id

        )

        if getattr(

            result,

            "isError",

            False

        ):

            return await self._parse_error(

                result

            )

        return self._parse_success(

            result

        )

    # ------------------------------------
    # Success
    # ------------------------------------

    def _parse_success(

        self,

        result

    ):

        try:

            data = json.loads(

                result.content[0].text

            )

            return {

                "success": True,

                "order_id": data["data"].get(

                    "orderId"

                ),

                "status": data["data"].get(

                    "status"

                ),

                "payment": data["data"].get(

                    "paymentMethod"

                ),

                "total": data["data"].get(

                    "cartTotal"

                ),

                "message": data.get(

                    "message",

                    "Order placed successfully."

                )

            }

        except Exception:

            return {

                "success": False,

                "code": "INVALID_RESPONSE",

                "message": "Unable to parse Swiggy checkout response."

            }

    # ------------------------------------
    # Error
    # ------------------------------------

    async def _parse_error(

        self,

        result

    ):

        error = ""

        if result.content:

            error = result.content[0].text

        decision = await checkout_recovery.execute(

            error

        )

        code = "UNKNOWN"

        if "Max Per Item Quantity Limit" in error:

            code = "LIMIT_EXCEEDED"

        elif "Out of Stock" in error:

            code = "OUT_OF_STOCK"

        elif "partially available" in error.lower():

            code = "PARTIAL_AVAILABILITY"

        elif "store is currently unavailable" in error.lower():

            code = "STORE_UNAVAILABLE"

        return {

            "success": False,

            "code": code,

            "message": error,

            "decision": decision

        }