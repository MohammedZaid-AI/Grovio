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


def _pick_variant_first(product: dict, *keys):
    """Read from the VARIANT first, then the product.

    Ordering ids must resolve this way round: the variant's skuId identifies the
    specific pack being bought, while the product-level one is its parent. Taking
    the parent adds the wrong thing — or nothing — to the cart.
    """
    return _first(_variant(product), *keys) or _first(product, *keys)


def _price_of(product: dict):
    """Instamart nests price as variations[0].price.offerPrice — an OBJECT, not
    a number. Passing that object to a float conversion yields None, which is
    why options rendered with no ₹ at all.

    Variant first, for the same reason skuId is: the price of the pack being
    bought, not the parent product's headline number.
    """
    raw = _pick_variant_first(product, "price", "offerPrice", "storePrice", "mrp")
    if isinstance(raw, dict):
        raw = _first(raw, "offerPrice", "storePrice", "price", "mrp")
    return _to_float(raw)


# Ordering needs BOTH ids: update_cart silently adds nothing when skuId is
# missing, and the emptiness only surfaces as a checkout failure. Offer.id is a
# single opaque handle, so both are packed into it and only this module reads it.
_ID_SEP = "::"


def _pack_id(spin_id, sku_id) -> str:
    return f"{spin_id}{_ID_SEP}{sku_id or ''}"


def _unpack_id(packed: str):
    spin_id, _, sku_id = str(packed).partition(_ID_SEP)
    return spin_id, (sku_id or None)


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


# Swiggy states the real reason in prose. Classifying it decides whether a
# retry is worth anything: an out-of-stock item will fail identically three
# times, so it must surface as ItemUnavailable rather than a generic error.
_ITEM_PROBLEMS = (
    "out of stock", "unavailable", "sold out", "not serviceable",
    "max per item quantity limit", "quantity limit", "store is closed",
    "partially available",
)


def _is_item_problem(text: str) -> bool:
    lowered = (text or "").lower()
    return any(needle in lowered for needle in _ITEM_PROBLEMS)


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


def _flatten(obj) -> dict:
    """Merge a payload with its nested `data` object.

    Swiggy wraps the order under `data` but leaves some fields — notably the
    order id — at the TOP level. Returning only one of the two loses whichever
    half the id happens to be in, and a lost id reads as a failed order.
    """
    if not isinstance(obj, dict):
        return {}
    inner = obj.get("data")
    return {**obj, **inner} if isinstance(inner, dict) else dict(obj)


def _payload_of(result) -> dict:
    """Pull the data dict out of an MCP result.

    The 2025-11-25 protocol may return structuredContent=None and put JSON in
    content[0].text instead, so both shapes are handled — the same defensive
    posture that fixed grocery search.
    """
    structured = getattr(result, "structuredContent", None)
    if isinstance(structured, dict) and structured:
        return _flatten(structured)

    content = getattr(result, "content", None)
    if content and getattr(content[0], "text", None):
        text = content[0].text
        # Only parse what actually looks like JSON: a human-readable
        # confirmation put through json.loads used to raise and lose the order.
        if str(text).lstrip()[:1] in ("{", "["):
            try:
                return _flatten(json.loads(text))
            except (TypeError, ValueError):
                return {}
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
            spin_id = _pick_variant_first(product, "spinId", "spin_id")
            if not (title and spin_id):
                continue

            price = _price_of(product)
            if ctx.max_price is not None and price is not None and price > ctx.max_price:
                continue

            offers.append(
                Offer(
                    provider=self.name,
                    kind=self.kind,
                    id=_pack_id(spin_id, _pick_variant_first(product, "skuId", "sku_id")),
                    title=str(title),
                    venue=_first(product, "brand", "storeName"),
                    price=price,
                    available=bool(_pick(product, "inStock", "available") is not False),
                    # quantityDescription ("500 g") is Instamart's own pack-size
                    # field and the one the pre-pivot listing showed. Without it
                    # every option renders as a bare name.
                    tags=tuple(filter(None, [_pick_variant_first(
                        product, "quantityDescription", "quantity", "packSize", "weight")])),
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
        spin_id, sku_id = _unpack_id(offer.id)

        # Start from an empty cart: a leftover line from a previous failed
        # attempt makes checkout fail in ways that look like this item's fault.
        try:
            await client.clear_cart()
        except Exception as e:
            logger.info(f"[{self.name}] clear_cart before add failed (continuing): {e!r}")

        # skuId is REQUIRED alongside spinId. Without it update_cart reports
        # success while adding nothing, and the empty cart only surfaces later
        # as an opaque checkout error.
        item = {"spinId": spin_id, "quantity": quantity}
        if sku_id:
            item["skuId"] = sku_id
        else:
            logger.warning(f"[{self.name}] no skuId for {spin_id} — the add may silently fail")

        cart = await client.update_cart(address_id, [item])
        if getattr(cart, "isError", False):
            logger.error(f"[{self.name}] cart rejected {item}: {_error_text(cart)}")
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
            detail = _error_text(result)
            logger.error(f"[{self.name}] checkout failed: {detail}")
            if _is_item_problem(detail):
                raise ItemUnavailable(f"checkout rejected {offer.id}: item problem")
            raise ProviderError(f"checkout failed for {offer.id}")

        # isError is False, so Swiggy ACCEPTED the order. Everything below is
        # about how much detail we can report — never about whether it happened.
        # Raising here because an id could not be parsed is what turned placed
        # orders into "that failed, shall I retry?" and risked ordering twice.
        payload = _payload_of(result)
        order_id = str(_first(payload, "orderId", "order_id", "id") or "")
        if not order_id:
            logger.warning(
                f"[{self.name}] order accepted but no id in the response. "
                f"Keys: {list(payload.keys())[:12]}"
            )

        return PlacedOrder(
            provider=self.name,
            order_id=order_id,
            status=PLACED,
            eta_minutes=_to_int(_first(payload, "etaMinutes", "eta", "deliveryTime")),
            # cartTotal first: it is the key Swiggy actually returns. Dropping it
            # is why confirmations reported no amount.
            total=_to_float(_first(payload, "cartTotal", "orderTotal", "grandTotal",
                                   "total", "amount")),
            items=(offer.title,),
        )
