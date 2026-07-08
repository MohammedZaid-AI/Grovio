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
    # Payment Options
    # ------------------------------------

    async def get_payment_options(self):

        client = await self.initialize()

        result = await client.get_payment_options()

        if getattr(result, "isError", False):

            # Log full detail server-side to diagnose tool name / argument issues.
            raw_text = None
            content = getattr(result, "content", None)
            if content and getattr(content[0], "text", None):
                raw_text = content[0].text
            print(f"[SwiggyService] get_payment_options FAILED. "
                  f"text={raw_text!r} structured={getattr(result, 'structuredContent', None)!r}")

            return {
                "success": False,
                "message": "⚠️ We couldn't load the available payment methods right now. Please try again in a few minutes."
            }

        return self._parse_payment_options(result)

    def _parse_payment_options(self, result):
        """Normalize the Swiggy get_payment_options response into a simple list.

        Returns:
            {"success": True, "options": [{"id": <method value>, "label": <text>}, ...]}
            or a friendly error dict on failure.

        Defensive: the exact Swiggy schema for payment options is not fixed, so
        we look for the list under several common keys and extract an id/label
        from several common field names.
        """
        try:
            structured = getattr(result, "structuredContent", None) or {}

            raw_list = None
            for key in ("paymentOptions", "payment_options", "paymentMethods",
                        "payment_methods", "options", "methods"):
                if isinstance(structured.get(key), list):
                    raw_list = structured[key]
                    break

            # Fallback: parse the JSON text payload.
            if raw_list is None:
                content = getattr(result, "content", None)
                if content and getattr(content[0], "text", None):
                    data = json.loads(content[0].text)
                    payload = data.get("data", data) if isinstance(data, dict) else data
                    if isinstance(payload, dict):
                        for key in ("paymentOptions", "payment_options", "paymentMethods",
                                    "payment_methods", "options", "methods"):
                            if isinstance(payload.get(key), list):
                                raw_list = payload[key]
                                break
                    elif isinstance(payload, list):
                        raw_list = payload

            options = []
            for entry in (raw_list or []):
                if isinstance(entry, str):
                    options.append({"id": entry, "label": entry})
                    continue
                method_id = (
                    entry.get("id") or entry.get("method") or entry.get("value")
                    or entry.get("code") or entry.get("type") or entry.get("paymentMethod")
                )
                label = (
                    entry.get("displayName") or entry.get("name") or entry.get("label")
                    or entry.get("title") or method_id
                )
                if method_id:
                    options.append({"id": method_id, "label": label})

            if not options:
                print(f"[SwiggyService] No payment options parsed from result: {structured}")
                return {
                    "success": False,
                    "message": "We couldn't load the available payment methods right now. Please try again in a few minutes."
                }

            return {"success": True, "options": options}

        except Exception as e:
            print(f"[SwiggyService] Exception parsing payment options: {e}")
            return {
                "success": False,
                "message": "We couldn't load the available payment methods right now. Please try again in a few minutes."
            }

    # ------------------------------------
    # Checkout
    # ------------------------------------

    async def checkout(self, payment_method=None):

        client = await self.initialize()

        address_id = await client.get_address_id()

        result = await client.checkout(

            address_id,

            payment_method=payment_method

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
                    "message": "⚠️ We couldn't reach the store to place your order. Please try again in a few minutes."
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
                    "message": "⚠️ We couldn't reach the store to place your order. Please try again in a few minutes."
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
                    "message": "⚠️ We couldn't reach the store to place your order. Please try again in a few minutes."
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

                "message": "⚠️ We couldn't confirm your order with the store. Please try again in a few minutes."

            }

    # ------------------------------------
    # Error
    # ------------------------------------

    # User-facing messages per error code. The raw Swiggy error (which can
    # contain internal Report IDs like "ERR-MRBXZQHQ...") is NEVER shown to
    # the user — it is only logged server-side. See fix for the raw-error leak.
    _FRIENDLY_ERROR_MESSAGES = {
        "LIMIT_EXCEEDED": "⚠️ One or more items exceed the maximum quantity allowed per order. Please reduce the quantity and try again.",
        "OUT_OF_STOCK": "⚠️ One or more items are out of stock right now. Please try again later or pick an alternative.",
        "PARTIAL_AVAILABILITY": "⚠️ Some items are only partially available. I can help you adjust the order.",
        "STORE_UNAVAILABLE": "⚠️ The store is currently unavailable. Please try again in a little while.",
        "NO_PAYMENT_METHOD": "⚠️ Please choose a payment method before placing the order.",
        "UNKNOWN": "⚠️ We couldn't complete the checkout right now. Please try again in a few minutes.",
    }

    async def _parse_error(

        self,

        result

    ):

        error = ""

        if result.content:

            error = result.content[0].text

        # Log the raw error server-side for debugging (never sent to the user).
        print(f"[SwiggyService] Raw checkout error from Swiggy MCP: {error}")

        decision = await checkout_recovery.execute(

            error

        )

        error_lower = error.lower()

        code = "UNKNOWN"

        if "Max Per Item Quantity Limit" in error:

            code = "LIMIT_EXCEEDED"

        elif "Out of Stock" in error:

            code = "OUT_OF_STOCK"

        elif "partially available" in error_lower:

            code = "PARTIAL_AVAILABILITY"

        elif "store is currently unavailable" in error_lower:

            code = "STORE_UNAVAILABLE"

        elif "payment method" in error_lower:

            code = "NO_PAYMENT_METHOD"

        return {

            "success": False,

            "code": code,

            # Clean, user-friendly message only. Raw error kept separately for logs.
            "message": self._FRIENDLY_ERROR_MESSAGES.get(code, self._FRIENDLY_ERROR_MESSAGES["UNKNOWN"]),

            "raw_error": error,

            "decision": decision

        }