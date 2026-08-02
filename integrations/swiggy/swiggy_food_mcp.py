"""
Swiggy Food MCP client — restaurants.

Separate from the Instamart client because it is a different server with a
different tool surface.

⚠️ TOOL NAMES ARE UNVERIFIED. The names in TOOLS below are placeholders following
Instamart's naming convention. Confirm them with:

    PYTHONPATH=. python integrations/swiggy/inspect_tools.py food

and correct TOOLS. The client VERIFIES them against the server on connect and
refuses to run if they don't exist — it will never silently call a wrong tool or
invent a result. `missing_tools` on the raised error names exactly what to fix.
"""
import json
import os

from mcp_use import MCPClient

from core.logger import logger

SERVER_URL = os.getenv("SWIGGY_FOOD_MCP_URL", "https://mcp.swiggy.com/food")

# ---------------------------------------------------------------------------
# The ONLY place tool names live. Correct these from inspect_tools.py output.
# ---------------------------------------------------------------------------
TOOLS = {
    "addresses":  os.getenv("SWIGGY_FOOD_TOOL_ADDRESSES",  "get_addresses"),
    "search":     os.getenv("SWIGGY_FOOD_TOOL_SEARCH",     "search_restaurants"),
    "menu":       os.getenv("SWIGGY_FOOD_TOOL_MENU",       "get_menu"),
    "update_cart": os.getenv("SWIGGY_FOOD_TOOL_CART",      "update_cart"),
    "checkout":   os.getenv("SWIGGY_FOOD_TOOL_CHECKOUT",   "checkout"),
    "order_status": os.getenv("SWIGGY_FOOD_TOOL_STATUS",   "get_order_status"),
}

# Without these the provider cannot do its job; the rest are optional niceties.
REQUIRED = ("search", "update_cart", "checkout")


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
        if not self.supports("addresses"):
            return None
        payload = payload_of(await self.call("addresses", {}))
        addresses = items_of(payload, "addresses", "items")
        return (addresses[0].get("id") if addresses and isinstance(addresses[0], dict) else None)

    async def search(self, query: str, address_id=None):
        args = {"query": query}
        if address_id:
            args["addressId"] = address_id
        return payload_of(await self.call("search", args))
