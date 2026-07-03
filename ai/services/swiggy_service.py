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
            # Safety guards for empty or malformed result
            if result is None:
                print("❌ Swiggy MCP checkout result is None.")
                return {
                    "success": False,
                    "order_placed": False,
                    "code": "EMPTY_RESPONSE",
                    "message": "Empty response from Swiggy MCP — check authentication/session validity. Couldn't reach the store, please retry."
                }
            
            content = getattr(result, "content", None)
            if not content:
                print(f"❌ Swiggy MCP checkout result has no content. Result: {result}")
                if hasattr(result, "structuredContent") and result.structuredContent:
                    print(f"Structured content: {result.structuredContent}")
                return {
                    "success": False,
                    "order_placed": False,
                    "code": "EMPTY_RESPONSE",
                    "message": "Empty response from Swiggy MCP — check authentication/session validity. Couldn't reach the store, please retry."
                }
            
            raw_text = content[0].text if hasattr(content[0], "text") else getattr(content[0], "text", None)
            
            print(f"🔍 Swiggy MCP raw text response: {raw_text}")
            
            if raw_text is None or (isinstance(raw_text, str) and not raw_text.strip()):
                print("❌ Swiggy MCP checkout response text is empty or whitespace-only!")
                print(f"Raw Result: {result}")
                if hasattr(result, "structuredContent") and result.structuredContent:
                    print(f"Structured content: {result.structuredContent}")
                return {
                    "success": False,
                    "order_placed": False,
                    "code": "EMPTY_RESPONSE",
                    "message": "Empty response from Swiggy MCP — check authentication/session validity. Couldn't reach the store, please retry."
                }

            data = json.loads(raw_text)

            return {

                "success": True,

                "order_placed": True,

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

        except Exception as e:
            print(f"❌ Exception in _parse_success parsing Swiggy checkout response: {e}")
            print(f"Raw Result: {result}")
            if result and hasattr(result, "content") and result.content:
                print(f"Raw Content[0].text: {result.content[0].text if hasattr(result.content[0], 'text') else getattr(result.content[0], 'text', None)}")

            return {

                "success": False,

                "order_placed": False,  # Set to False so the session is NOT marked as checked out / ended

                "code": "INVALID_RESPONSE",

                "message": f"Unable to parse Swiggy checkout response: {str(e)}. Couldn't reach the store, please retry."

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