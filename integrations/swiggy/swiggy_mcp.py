import asyncio
import json
import os
from mcp_use import MCPClient
from pathlib import Path

# Temporary, gated instrumentation (Swiggy cart investigation).
# Off by default; enable live with  SWIGGY_MCP_DEBUG=1  to capture the full
# request/response + store context of every MCP call. Pure logging — no
# behaviour change.
SWIGGY_MCP_DEBUG = os.getenv("SWIGGY_MCP_DEBUG", "0") not in ("0", "", "false", "False")

# Store/merchant identifiers we want to trace through the flow. If search
# returns any of these at the top level and update_cart drops them, this is the
# lost-context bug.
_CONTEXT_KEYS = (
    "storeid", "store_id", "merchantid", "merchant_id", "outletid", "outlet_id",
    "warehouseid", "warehouse_id", "locationid", "location_id", "fulfillmentid",
    "catalogid", "inventoryid", "storestatus", "store_status", "isopen", "closed",
)


def _mcp_dump(obj):
    try:
        return json.dumps(obj, indent=2, default=str)[:6000]
    except Exception:
        return repr(obj)[:6000]


def _scan_context(obj, depth=0):
    """Recursively collect any store/merchant/outlet-like identifiers so we can
    see, per call, what store context the response actually carries."""
    found = {}
    if depth > 5:
        return found
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k.lower() in _CONTEXT_KEYS and not isinstance(v, (dict, list)):
                found[k] = v
            for sk, sv in _scan_context(v, depth + 1).items():
                found.setdefault(sk, sv)
    elif isinstance(obj, list) and obj:
        for sk, sv in _scan_context(obj[0], depth + 1).items():
            found.setdefault(sk, sv)
    return found


class SwiggyInstamart:

    def __init__(self):

        config_path = Path(__file__).parent / "mcp.json"

        self.client = MCPClient.from_config_file(
            str(config_path)
        )

        self.session = None

    async def initialize(self):
        sessions = await self.client.create_all_sessions()
        self.session = sessions["instamart"]
        return self

    async def _call_tool(self, name, args):
        """Single choke-point for every MCP tool call so the whole request/
        response sequence + store context can be traced in one place."""
        if SWIGGY_MCP_DEBUG:
            print(f"\n[MCP -> ] {name}")
            print(f"          request  = {_mcp_dump(args)}")
        result = await self.session.call_tool(name, args)
        if SWIGGY_MCP_DEBUG:
            sc = getattr(result, "structuredContent", None)
            ctx = _scan_context(sc)
            print(f"[MCP <- ] {name}  isError={getattr(result, 'isError', None)}")
            if ctx:
                print(f"          context  = {ctx}")
            content = getattr(result, "content", None)
            if content and getattr(content[0], "text", None):
                print(f"          text     = {content[0].text[:2000]}")
            print(f"          struct   = {_mcp_dump(sc)}")
        return result

    async def get_default_address(self):
        return await self._call_tool(
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

        return await self._call_tool(
            "search_products",
            {
                "addressId": address_id,
                "query": query
            }
        )

    async def clear_cart(self):

        return await self._call_tool(
            "clear_cart",
            {}
        )

    async def get_cart(self):

        return await self._call_tool(
            "get_cart",
            {}
        )

    async def update_cart(
        self,
        address_id,
        items
    ):

        return await self._call_tool(
            "update_cart",
            {
                "selectedAddressId": address_id,
                "items": items
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
            [
                {
                    "spinId": spin_id,
                    "quantity": quantity
                }
            ]
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

        address_id = await self.get_address_id()

        payload = []

        for item in items:

            options = await self.get_product_options(item["name"])

            if not options:

                print(f"Product not found: {item['name']}")

                continue

            product = options[0]

            variant = product["variations"][0]

            spin_id = variant["spinId"]

            payload.append({

                "spinId": spin_id,

                "quantity": item["quantity"]

            })

        if payload:

            result = await self.update_cart(address_id, payload)

            print("\nUPDATE CART RESULT:")

            print(result)

        cart = await self.get_cart()

        print("\nCURRENT CART:")

        print(cart)

        return cart

    async def get_payment_options(
    self,
    address_id=None
    ):

        # Payment options are address-scoped on Swiggy Instamart, so pass the
        # same address used for the cart/checkout.
        if address_id is None:
            address_id = await self.get_address_id()

        return await self._call_tool(
            "get_payment_options",
            {
                "addressId": address_id
            }
        )

    async def checkout(
    self,
    address_id,
    payment_method=None,
    intent_app=None,
    generate_upi_qr=False
    ):

        payload = {
        "addressId": address_id
        }

        # paymentMethod is a GROUP value ("UPI", "Cash"/"COD", "SwiggyPay").
        # For a UPI app, pass paymentMethod="UPI" + intentApp=<app id>.
        if payment_method:
            payload["paymentMethod"] = payment_method

        if intent_app:
            payload["intentApp"] = intent_app

        if generate_upi_qr:
            payload["generateUPIQR"] = True

        return await self._call_tool(
            "checkout",
            payload
        )
    
    async def get_product_options(
    self,
    item_name
    ):

        address_id = await self.get_address_id()

        search_result = await self.search_product(
            address_id,
            item_name
        )

        if getattr(
            search_result,
            "isError",
            False
        ):
            return []

        # The payload may arrive as structuredContent (older MCP) or as JSON text
        # in content[0].text (newer 2025-11-25 protocol -> structuredContent=None),
        # and products may be top-level or nested under "data". Handle all shapes.
        def _products_from(obj):
            if isinstance(obj, dict):
                if isinstance(obj.get("products"), list):
                    return obj["products"]
                inner = obj.get("data")
                if isinstance(inner, dict) and isinstance(inner.get("products"), list):
                    return inner["products"]
            return None

        products = _products_from(getattr(search_result, "structuredContent", None))
        if products is not None:
            return products

        content = getattr(search_result, "content", None)
        if content and getattr(content[0], "text", None):
            try:
                products = _products_from(json.loads(content[0].text))
                if products is not None:
                    return products
            except Exception:
                pass

        print(f"[Swiggy] search {item_name!r}: no products parsed")
        return []
