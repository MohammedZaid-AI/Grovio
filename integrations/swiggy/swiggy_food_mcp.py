"""
Swiggy Food MCP client — restaurants.

Separate from the Instamart client because it is a different server with a
different tool surface.

Tool names below are VERIFIED against the live server (18 tools, confirmed
2026-08-02 via inspect_tools.py). The client still re-checks them on connect and
refuses to run if the surface changes — it will never silently call a wrong tool
or invent a result.

Swiggy's own tool descriptions state a required workflow: **get_addresses must
be called first**, and every subsequent call carries that addressId.

Ordering path:
    get_addresses → search_menu → update_food_cart → place_food_order
`search_menu` is the primary search rather than `search_restaurants`, because it
returns individual dishes with prices — the things a cart can actually hold.
"""
import json
import os

from mcp_use import MCPClient

from core.logger import logger

SERVER_URL = os.getenv("SWIGGY_FOOD_MCP_URL", "https://mcp.swiggy.com/food")

# ---------------------------------------------------------------------------
# The ONLY place tool names live. Verified against the live server.
# ---------------------------------------------------------------------------
TOOLS = {
    "addresses":    "get_addresses",        # must be called first
    "search":       "search_menu",          # dishes with prices — orderable
    "restaurants":  "search_restaurants",   # restaurant-level results
    "menu":         "get_restaurant_menu",
    "cart":         "get_food_cart",
    "update_cart":  "update_food_cart",
    "flush_cart":   "flush_food_cart",
    "checkout":     "place_food_order",
    "orders":       "get_food_orders",
    "order_status": "track_food_order",
    "coupons":      "fetch_food_coupons",
}

# Without these the provider cannot do its job; the rest are optional niceties.
REQUIRED = ("addresses", "search", "update_cart", "checkout")

# COD only for now. Swiggy's UPI path involves a polling widget plus
# check_payment_status/confirm_order, which does not fit a chat window.
PAYMENT_METHOD = "Cash"


class NoDeliveryAddress(RuntimeError):
    """The account has no saved address, so nothing can be searched or ordered.
    Recoverable by the user — they add one in the Swiggy app."""


class ToolSurfaceMismatch(RuntimeError):
    """The server does not expose the tools we were configured to call."""

    def __init__(self, missing, available):
        self.missing_tools = missing
        self.available_tools = available
        super().__init__(
            f"Swiggy Food MCP is missing expected tools {missing}. "
            f"Server actually exposes: {available}. "
            f"Correct TOOLS in integrations/swiggy/swiggy_food_mcp.py."
        )


def payload_of(result) -> dict:
    """Extract the data dict from an MCP result.

    Handles both shapes: structuredContent, and JSON-in-text under content[0]
    (the 2025-11-25 protocol returns structuredContent=None), with or without a
    "data" wrapper.
    """
    def unwrap(obj):
        if not isinstance(obj, dict):
            return None
        inner = obj.get("data")
        return inner if isinstance(inner, dict) else obj

    unwrapped = unwrap(getattr(result, "structuredContent", None))
    if unwrapped is not None:
        return unwrapped

    content = getattr(result, "content", None)
    if content and getattr(content[0], "text", None):
        try:
            return unwrap(json.loads(content[0].text)) or {}
        except (TypeError, ValueError):
            return {}
    return {}


def items_of(payload: dict, *keys) -> list:
    """Pull the first list found under any of `keys`, at the top level or one
    level down. Providers nest inconsistently."""
    for key in keys:
        value = payload.get(key)
        if isinstance(value, list):
            return value
    for value in payload.values():
        if isinstance(value, dict):
            for key in keys:
                nested = value.get(key)
                if isinstance(nested, list):
                    return nested
    return []


class SwiggyFood:
    """Thin MCP wrapper. Verifies its tool surface before doing anything."""

    def __init__(self, url: str = None):
        self.url = url or SERVER_URL
        self.client = MCPClient.from_dict({"mcpServers": {"food": {"url": self.url}}})
        self.session = None
        self.available = ()
        self._address_id = None

    async def initialize(self):
        sessions = await self.client.create_all_sessions()
        self.session = sessions["food"]

        tools = await self.session.list_tools()
        self.available = tuple(getattr(t, "name", str(t)) for t in tools)

        missing = [TOOLS[k] for k in REQUIRED if TOOLS[k] not in self.available]
        if missing:
            # Fail loudly rather than call a tool that doesn't exist. The layers
            # above turn this into "I can't search restaurants yet".
            raise ToolSurfaceMismatch(missing, list(self.available))

        logger.info(f"[swiggy_food] connected — {len(self.available)} tools")
        return self

    def supports(self, key: str) -> bool:
        return TOOLS.get(key) in self.available

    async def call(self, key: str, args: dict):
        name = TOOLS[key]
        if name not in self.available:
            raise ToolSurfaceMismatch([name], list(self.available))
        return await self.session.call_tool(name, args)

    # -- convenience wrappers -------------------------------------------------
    async def default_address_id(self):
        """Swiggy requires this before anything else; results are sorted with
        the most recently used address first, which is the right default."""
        if self._address_id:
            return self._address_id

        payload = payload_of(await self.call("addresses", {}))
        addresses = items_of(payload, "addresses", "items", "results")
        if not addresses or not isinstance(addresses[0], dict):
            raise NoDeliveryAddress(
                "This Swiggy account has no saved delivery address."
            )

        first = addresses[0]
        self._address_id = str(
            first.get("id") or first.get("addressId") or first.get("address_id") or ""
        )
        if not self._address_id:
            raise NoDeliveryAddress("Swiggy returned an address without an id.")
        return self._address_id

    async def search_dishes(self, query: str, address_id: str, offset: int = 0):
        """Dishes matching a craving, across restaurants."""
        args = {"addressId": address_id, "query": query}
        if offset:
            args["offset"] = offset
        return payload_of(await self.call("search", args))

    async def add_to_cart(self, address_id: str, restaurant_id: str, cart_items: list,
                          restaurant_name: str = None):
        args = {
            "addressId": address_id,
            "restaurantId": restaurant_id,
            "cartItems": cart_items,
        }
        if restaurant_name:
            args["restaurantName"] = restaurant_name
        return await self.call("update_cart", args)

    async def place_order(self, address_id: str, payment_method: str = PAYMENT_METHOD):
        return await self.call("checkout", {
            "addressId": address_id,
            "paymentMethod": payment_method,
        })

    async def track(self, order_id: str):
        return payload_of(await self.call("order_status", {"orderId": order_id}))
