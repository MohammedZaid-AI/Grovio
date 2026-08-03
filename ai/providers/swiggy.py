"""
Swiggy Instamart provider — GROCERY only.

This is the one file permitted to know Swiggy exists. It translates Instamart's
product payloads into neutral `Offer`s and nothing above it can tell which
platform answered.

SCOPE: Instamart is a *grocery* catalogue. It has no restaurants, menus, ratings
or delivery ETAs, so this provider registers under ProviderKind.GROCERY and
leaves `rating`/`eta_minutes` as None rather than fabricating them. Restaurant
search needs a different provider — see MIGRATION.md §0.
"""
import asyncio
import json

from core.logger import logger

from ai.providers.base import (
    PLACED,
    ItemUnavailable,
    Offer,
    PlacedOrder,
    ProviderError,
    ProviderKind,
    SearchContext,
)
from ai.providers.oauth import OAuthConfig
from integrations.swiggy.swiggy_mcp import SwiggyInstamart

# Instamart MCP endpoint. Swiggy also runs /food and /dineout — see
# FEASIBILITY.md; adding them means new adapters here, nothing above.
INSTAMART_SERVER = "https://mcp.swiggy.com/im"


def _to_float(value):
    """Prices arrive as numbers, strings, or paise. Unparseable -> None (unknown)
    rather than 0, which would read as 'free'."""
    if value is None:
        return None
    try:
        number = float(str(value).replace("₹", "").replace(",", "").strip())
    except (TypeError, ValueError):
        return None
    return number if number > 0 else None


def _to_int(value):
    try:
        return int(float(str(value).strip()))
    except (TypeError, ValueError):
        return None


def _first(product: dict, *keys):
    if not isinstance(product, dict):
        return None
    for key in keys:
        value = product.get(key)
        if value not in (None, "", []):
            return value
    return None


def _variant(product: dict) -> dict:
    """The orderable variant of a product.

    Instamart returns a product with one or more `variations` (pack sizes), and
    the fields that matter for ordering — spinId, price, stock — live on the
    VARIANT, not the product. Looking only at the top level silently loses them.
    """
    variations = product.get("variations")
    if isinstance(variations, list) and variations and isinstance(variations[0], dict):
        return variations[0]
    return {}


def _pick(product: dict, *keys):
    """Read a field from the product, falling back to its first variant."""
    return _first(product, *keys) or _first(_variant(product), *keys)


def _cart_has_items(payload: dict) -> bool:
    """True if the cart payload shows at least one line item, whatever the
    provider chose to call the list."""
    if not isinstance(payload, dict):
        return False
    for key in ("items", "cartItems", "products", "lineItems"):
        value = payload.get(key)
        if isinstance(value, list) and value:
            return True
    for value in payload.values():
        if isinstance(value, dict) and _cart_has_items(value):
            return True
    # Some carts report only a count/total.
    for key in ("itemCount", "totalItems", "count"):
        if _to_float(payload.get(key)):
            return True
    return False


def _error_text(result) -> str:
    """Pull a provider's own error message out of an MCP result.

    For logs only — never shown to a user. Without this a failure is just
    `isError=True`, which is impossible to act on.
    """
    content = getattr(result, "content", None)
    if content and getattr(content[0], "text", None):
        return str(content[0].text)[:400]
    structured = getattr(result, "structuredContent", None)
    if structured:
        return str(structured)[:400]
    return "<no error detail returned>"


def _payload_of(result) -> dict:
    """Pull the data dict out of an MCP result.

    The 2025-11-25 protocol may return structuredContent=None and put JSON in
    content[0].text instead, so both shapes are handled — the same defensive
    posture that fixed grocery search.
    """
    structured = getattr(result, "structuredContent", None)
    if isinstance(structured, dict):
        return structured.get("data") if isinstance(structured.get("data"), dict) else structured

    content = getattr(result, "content", None)
    if content and getattr(content[0], "text", None):
        try:
            parsed = json.loads(content[0].text)
        except (TypeError, ValueError):
            return {}
        if isinstance(parsed, dict):
            return parsed.get("data") if isinstance(parsed.get("data"), dict) else parsed
    return {}


class SwiggyInstamartProvider:
    """Grocery search over the Instamart MCP endpoint.

    Acts on behalf of an individual user, so it is a LinkableProvider: it
    declares where its OAuth server lives and lets the generic engine do the
    rest. Endpoints are DISCOVERED per RFC 8414/9728 — none are hardcoded here,
    because inventing them would be inventing an API.

    SWIGGY_OAUTH_CLIENT_ID is issued with Builders Club production access. Until
    then discovery may offer dynamic registration, or local prototyping runs
    against a whitelisted localhost redirect. See FEASIBILITY.md.
    """

    name = "swiggy_instamart"
    display_name = "Swiggy"
    kind = ProviderKind.GROCERY

    # Instamart's MCP exposes cart + checkout, but no order-status or
    # cancellation tool. Declaring that honestly is what lets the layers above
    # say "I can't see live status" instead of inventing one.
    supports_tracking = False
    supports_cancellation = False

    # MVP is cash on delivery: it needs no stored payment instrument and no
    # UPI intent round-trip through a chat window.
    PAYMENT_METHOD = "Cash"

    oauth = OAuthConfig(
        server_url=INSTAMART_SERVER,
        client_id_env="SWIGGY_OAUTH_CLIENT_ID",
        client_secret_env="SWIGGY_OAUTH_CLIENT_SECRET",
    )

    def __init__(self):
        self._client = None
        self._lock = asyncio.Lock()

    async def _session(self):
        """One MCP session, created on first use and reused. Guarded so
        concurrent turns don't open duplicate sessions."""
        async with self._lock:
            if self._client is None:
                self._client = await SwiggyInstamart().initialize()
            return self._client

    async def search(self, query: str, ctx: SearchContext) -> list[Offer]:
        try:
            client = await self._session()
            products = await client.get_product_options(query)
        except Exception as e:
            # Drop the session so the next turn reconnects rather than reusing
            # a dead one. The registry logs and skips us.
            self._client = None
            logger.error(f"[{self.name}] search failed: {e!r}")
            raise

        offers = []
        for product in products[: ctx.limit]:
            if not isinstance(product, dict):
                continue

            title = _first(product, "name", "displayName", "productName")
            # The cart is keyed by spinId. Anything without one cannot be
            # ordered, so it is not a real offer no matter how good it looks.
            spin_id = _pick(product, "spinId", "spin_id")
            if not (title and spin_id):
                continue

            price = _to_float(_pick(product, "offerPrice", "price", "storePrice", "mrp"))
            if ctx.max_price is not None and price is not None and price > ctx.max_price:
                continue

            offers.append(
                Offer(
                    provider=self.name,
                    kind=self.kind,
                    id=str(spin_id),
                    title=str(title),
                    venue=_first(product, "brand", "storeName"),
                    price=price,
                    available=bool(_pick(product, "inStock", "available") is not False),
                    tags=tuple(filter(None, [_pick(product, "quantity", "packSize", "weight")])),
                )
            )

        if products and not offers:
            logger.warning(
                f"[{self.name}] {len(products)} products returned but none were "
                f"orderable (no spinId found). First product keys: "
                f"{list(products[0].keys()) if isinstance(products[0], dict) else type(products[0])}"
            )
        return offers

    # ------------------------------------------------------------------
    # Ordering
    # ------------------------------------------------------------------
    async def place(self, offer: Offer, quantity: int, ctx: SearchContext) -> PlacedOrder:
        """Cart the chosen item and check out with cash on delivery.

        Raises ItemUnavailable when the store or item won't accept it, and
        ProviderError otherwise — the layers above turn both into plain English.
        """
        client = await self._session()
        address_id = await client.get_address_id()

        cart = await client.update_cart(address_id, [{"spinId": offer.id, "quantity": quantity}])
        if getattr(cart, "isError", False):
            logger.error(f"[{self.name}] cart rejected {offer.id}: {_error_text(cart)}")
            raise ItemUnavailable(f"cart rejected {offer.id}")

        # Confirm the item actually landed. update_cart can return success while
        # adding nothing (an id the catalogue doesn't recognise), and the failure
        # then surfaces as an opaque checkout error several calls later.
        cart_payload = _payload_of(await client.get_cart())
        if not _cart_has_items(cart_payload):
            logger.error(
                f"[{self.name}] cart empty after adding {offer.id} — the item id "
                f"was not accepted. Cart keys: {list(cart_payload.keys())[:12]}"
            )
            raise ItemUnavailable(f"{offer.id} could not be added to the cart")

        result = await client.checkout(address_id, payment_method=self.PAYMENT_METHOD)
        if getattr(result, "isError", False):
            logger.error(f"[{self.name}] checkout failed: {_error_text(result)}")
            raise ProviderError(f"checkout failed for {offer.id}")

        payload = _payload_of(result)
        order_id = str(_first(payload, "orderId", "order_id", "id") or "")
        if not order_id:
            # No id means we cannot prove an order exists. Treat it as a failure
            # rather than telling someone their food is on the way.
            raise ProviderError("checkout returned no order id")

        return PlacedOrder(
            provider=self.name,
            order_id=order_id,
            status=PLACED,
            eta_minutes=_to_int(_first(payload, "etaMinutes", "eta", "deliveryTime")),
            total=_to_float(_first(payload, "total", "grandTotal", "orderTotal")),
            items=(offer.title,),
        )
