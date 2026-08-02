"""
Swiggy Food provider — restaurants.

Turns Swiggy's Food MCP responses into neutral Offers. Nothing above this file
learns which platform answered.

⚠️ Registered ONLY when SWIGGY_FOOD_ENABLED=1, because the tool names it calls
are unverified (see integrations/swiggy/swiggy_food_mcp.py). Until then
restaurant search reports itself unavailable and the concierge says so — which
is correct, and far better than a stub returning invented venues.

Field extraction is deliberately forgiving: it tries several plausible key
names and leaves anything it cannot find as None. A missing rating stays
missing; it never becomes a number.
"""
import asyncio
import os

from core.logger import logger

from ai.providers.base import (
    PLACED,
    ItemUnavailable,
    Offer,
    OrderStatus,
    PlacedOrder,
    ProviderError,
    ProviderKind,
    SearchContext,
    UNKNOWN,
)
from ai.providers.oauth import OAuthConfig
from integrations.swiggy import swiggy_food_mcp as mcp


def _num(value):
    if value is None:
        return None
    try:
        number = float(str(value).replace("₹", "").replace(",", "").strip())
    except (TypeError, ValueError):
        return None
    return number if number > 0 else None


def _int(value):
    number = _num(value)
    return int(number) if number is not None else None


def _first(obj, *keys):
    if not isinstance(obj, dict):
        return None
    for key in keys:
        value = obj.get(key)
        if value not in (None, "", [], {}):
            return value
    return None


def _rating(entry):
    """Ratings arrive as a number or nested under an object. Anything outside a
    plausible 0–5 range is discarded rather than shown."""
    raw = _first(entry, "rating", "avgRating", "avgRatingString", "ratings")
    if isinstance(raw, dict):
        raw = _first(raw, "value", "rating", "aggregatedRating")
    value = _num(raw)
    return value if value is not None and 0 < value <= 5 else None


def _eta(entry):
    raw = _first(entry, "etaMinutes", "eta", "deliveryTime", "sla", "slaString")
    if isinstance(raw, dict):
        raw = _first(raw, "deliveryTime", "minutes", "value")
    if isinstance(raw, str):
        digits = "".join(c for c in raw if c.isdigit())
        raw = digits or None
    return _int(raw)


class SwiggyFoodProvider:
    """Restaurant search and ordering over the Swiggy Food MCP server."""

    name = "swiggy_food"
    display_name = "Swiggy"
    kind = ProviderKind.RESTAURANT

    # Swiggy documents order tracking on the Food server; whether the tool is
    # actually present is confirmed at connect time, so this is re-evaluated per
    # session rather than trusted blindly.
    supports_tracking = True
    supports_cancellation = False

    PAYMENT_METHOD = "Cash"   # COD only, per Swiggy's MCP documentation

    oauth = OAuthConfig(
        server_url=mcp.SERVER_URL,
        client_id_env="SWIGGY_OAUTH_CLIENT_ID",
        client_secret_env="SWIGGY_OAUTH_CLIENT_SECRET",
    )

    def __init__(self):
        self._client = None
        self._lock = asyncio.Lock()

    async def _session(self):
        async with self._lock:
            if self._client is None:
                client = await mcp.SwiggyFood().initialize()
                # Trust the server over our assumption.
                self.supports_tracking = client.supports("order_status")
                self._client = client
            return self._client

    def _drop_session(self):
        self._client = None

    async def search(self, query: str, ctx: SearchContext) -> list:
        try:
            client = await self._session()
            address_id = ctx.address_id or await client.default_address_id()
            payload = await client.search(query, address_id)
        except mcp.ToolSurfaceMismatch as e:
            self._drop_session()
            # Actionable: names exactly what to fix, and never fabricates a result.
            logger.error(f"[{self.name}] {e}")
            raise
        except Exception as e:
            self._drop_session()
            logger.error(f"[{self.name}] search failed: {e!r}")
            raise

        entries = mcp.items_of(payload, "restaurants", "results", "items", "cards", "dishes")
        offers = []
        for entry in entries[: ctx.limit]:
            if not isinstance(entry, dict):
                continue

            title = _first(entry, "name", "displayName", "title", "restaurantName")
            if not title:
                continue

            price = _num(_first(entry, "price", "costForTwo", "defaultPrice", "finalPrice"))
            if ctx.max_price is not None and price is not None and price > ctx.max_price:
                continue

            offers.append(Offer(
                provider=self.name,
                kind=self.kind,
                id=str(_first(entry, "id", "restaurantId", "itemId", "menuItemId") or title),
                title=str(title),
                venue=_first(entry, "restaurantName", "restaurant", "storeName"),
                price=price,
                rating=_rating(entry),
                eta_minutes=_eta(entry),
                distance_km=_num(_first(entry, "distanceKm", "distance")),
                available=_first(entry, "isOpen", "available", "isServiceable") is not False,
                tags=tuple(t for t in (_first(entry, "cuisine", "cuisines", "category"),) if t),
            ))
        return offers

    async def place(self, offer: Offer, quantity: int, ctx: SearchContext) -> PlacedOrder:
        client = await self._session()
        address_id = ctx.address_id or await client.default_address_id()

        cart = await client.call("update_cart", {
            "addressId": address_id,
            "items": [{"itemId": offer.id, "quantity": quantity}],
        })
        if getattr(cart, "isError", False):
            raise ItemUnavailable(f"cart rejected {offer.id}")

        result = await client.call("checkout", {
            "addressId": address_id,
            "paymentMethod": self.PAYMENT_METHOD,
        })
        if getattr(result, "isError", False):
            raise ProviderError("checkout rejected")

        payload = mcp.payload_of(result)
        order_id = str(_first(payload, "orderId", "order_id", "id") or "")
        if not order_id:
            # No id means we cannot prove an order exists. Never tell someone
            # their food is coming on the strength of an ambiguous response.
            raise ProviderError("checkout returned no order id")

        return PlacedOrder(
            provider=self.name,
            order_id=order_id,
            status=PLACED,
            eta_minutes=_eta(payload) or offer.eta_minutes,
            total=_num(_first(payload, "total", "grandTotal", "orderTotal")) or offer.price,
            items=(offer.title,),
        )

    async def track(self, order_id: str, ctx: SearchContext) -> OrderStatus:
        client = await self._session()
        if not client.supports("order_status"):
            return OrderStatus(order_id=order_id, status=UNKNOWN)

        payload = mcp.payload_of(await client.call("order_status", {"orderId": order_id}))
        raw = str(_first(payload, "status", "orderStatus", "state") or "").upper()

        # Map the platform's vocabulary onto ours; anything unrecognised stays
        # UNKNOWN rather than being guessed into a confident-sounding state.
        mapped = UNKNOWN
        for needle, state in (
            ("DELIVER", "DELIVERED"), ("CANCEL", "CANCELLED"),
            ("WAY", "ON_THE_WAY"), ("PICK", "ON_THE_WAY"), ("DISPATCH", "ON_THE_WAY"),
            ("PREPAR", "PREPARING"), ("COOK", "PREPARING"),
            ("CONFIRM", "CONFIRMED"), ("ACCEPT", "CONFIRMED"),
        ):
            if needle in raw:
                mapped = state
                break

        return OrderStatus(
            order_id=order_id,
            status=mapped,
            eta_minutes=_eta(payload),
        )


def enabled() -> bool:
    """Off unless explicitly switched on, because the tool names are unverified.
    Registering a restaurant provider that cannot actually search would make the
    concierge claim a capability it does not have."""
    return os.getenv("SWIGGY_FOOD_ENABLED", "").strip().lower() in ("1", "true", "yes")
