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
from ai.providers import swiggy
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


# Swiggy needs a restaurant id AND a menu item id to build a cart, but `Offer.id`
# is a single opaque handle by design. Packing both here keeps every layer above
# provider-agnostic — nothing else knows or parses this format.
_ID_SEP = "::"


def _pack_id(restaurant_id, item_id) -> str:
    return f"{restaurant_id}{_ID_SEP}{item_id}"


def _unpack_id(packed: str):
    restaurant_id, _, item_id = str(packed).partition(_ID_SEP)
    return (restaurant_id, item_id) if item_id else (None, restaurant_id)


# Swiggy states the real reason in prose. Classifying it decides whether a retry
# is worth anything: a sold-out dish fails identically every time, so it must
# surface as ItemUnavailable rather than a generic error.
_ITEM_PROBLEMS = (
    "out of stock", "unavailable", "sold out", "not serviceable", "closed",
    "quantity limit", "partially available",
)


def _is_item_problem(text: str) -> bool:
    lowered = (text or "").lower()
    return any(needle in lowered for needle in _ITEM_PROBLEMS)


def _error_text(result) -> str:
    """A provider's own error message, for LOGS only — never shown to a user.
    Without it a failure is just isError=True, which nobody can act on."""
    content = getattr(result, "content", None)
    if content and getattr(content[0], "text", None):
        return str(content[0].text)[:400]
    structured = getattr(result, "structuredContent", None)
    return str(structured)[:400] if structured else "<no error detail returned>"


def _price_of(entry):
    """A dish price, wherever Swiggy put it.

    Same shape hazards as Instamart: the number may be nested in a price object,
    or live on the first variant rather than the item. A price that fails to
    extract renders as a blank option, so this tries the item, its price object
    and its first variant before giving up.
    """
    raw = _first(entry, "price", "finalPrice", "defaultPrice", "displayPrice", "offerPrice")
    if isinstance(raw, dict):
        raw = _first(raw, "offerPrice", "finalPrice", "price", "displayPrice", "mrp")
    value = _num(raw)
    if value is not None:
        return value

    variants = entry.get("variants") or entry.get("variations")
    if isinstance(variants, list) and variants and isinstance(variants[0], dict):
        return _price_of(variants[0])
    return None


def _entries(payload):
    """Flatten a menu search into (item, restaurant) pairs.

    Swiggy returns dishes either flat — each carrying a nested restaurant
    object — or grouped under a restaurant card holding its matching items.
    Handling only the flat shape drops every result of the grouped one, which
    reads to the user as "nothing found". Anything else yields nothing rather
    than a guess.
    """
    pairs = []
    for entry in mcp.items_of(payload, "items", "dishes", "results", "menuItems",
                              "cards", "restaurants"):
        if not isinstance(entry, dict):
            continue

        nested = next(
            (entry[key] for key in ("items", "menuItems", "dishes", "menu")
             if isinstance(entry.get(key), list) and entry[key]),
            None,
        )
        if nested:
            pairs.extend((item, entry) for item in nested if isinstance(item, dict))
        else:
            restaurant = entry.get("restaurant")
            # {} rather than `entry` when there is no nested restaurant object:
            # falling back to the entry itself would resolve the RESTAURANT name
            # from the dish's own `name`, labelling every option with its own
            # title. The dish-level keys are read first anyway.
            pairs.append((entry, restaurant if isinstance(restaurant, dict) else {}))
    return pairs


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

    # Same authorization server as Instamart — Swiggy publishes one, at the
    # origin root, shared by every MCP server it runs. See ai/providers/swiggy.py
    # for the verification note.
    oauth = OAuthConfig(
        server_url=mcp.SERVER_URL,
        scopes=swiggy.SWIGGY_SCOPES,
        client_id_env="SWIGGY_OAUTH_CLIENT_ID",
        client_secret_env="SWIGGY_OAUTH_CLIENT_SECRET",
        authorize_url_env="SWIGGY_OAUTH_AUTHORIZE_URL",
        token_url_env="SWIGGY_OAUTH_TOKEN_URL",
        authorize_url=swiggy.SWIGGY_AUTHORIZE_URL,
        token_url=swiggy.SWIGGY_TOKEN_URL,
        registration_url=swiggy.SWIGGY_REGISTRATION_URL,
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
            payload = await client.search_dishes(query, address_id)
        except (mcp.ToolSurfaceMismatch, mcp.NoDeliveryAddress) as e:
            self._drop_session()
            logger.error(f"[{self.name}] {e}")
            raise
        except Exception as e:
            self._drop_session()
            logger.error(f"[{self.name}] search failed: {e!r}")
            raise

        pairs = _entries(payload)
        offers, unpriced = [], 0
        for entry, restaurant in pairs[: ctx.limit]:
            title = _first(entry, "name", "displayName", "itemName", "title")
            # The restaurant id may be on the dish or on the card grouping it.
            restaurant_id = (_first(entry, "restaurantId", "restaurant_id", "resId")
                             or _first(restaurant, "restaurantId", "resId", "id"))
            # `menu_item_id` is what search_menu actually returns — verified
            # against the live server 2026-08-16. Its absence from this list is
            # why every dish was dropped as "not orderable".
            item_id = _first(entry, "menu_item_id", "itemId", "menuItemId",
                             "dishId", "id")
            if not (title and restaurant_id and item_id):
                # Cannot be ordered without both ids, so it is not an offer.
                continue

            price = _price_of(entry)
            if price is None:
                unpriced += 1
            if ctx.max_price is not None and price is not None and price > ctx.max_price:
                continue

            offers.append(Offer(
                provider=self.name,
                kind=self.kind,
                # Opaque composite handle: ordering needs BOTH ids, and only
                # this module knows how to read it. Nothing above parses it.
                id=_pack_id(restaurant_id, item_id),
                title=str(title),
                # Rating and ETA describe the restaurant, so they usually live on
                # the card rather than the dish. Check both.
                venue=(_first(entry, "restaurant_name", "restaurantName", "storeName")
                       or _first(restaurant, "name", "restaurantName", "storeName")),
                price=price,
                rating=_rating(entry) or _rating(restaurant),
                eta_minutes=_eta(entry) or _eta(restaurant),
                distance_km=(_num(_first(entry, "distanceKm", "distance"))
                             or _num(_first(restaurant, "distanceKm", "distance"))),
                available=_first(entry, "inStock", "isAvailable", "available") is not False,
                tags=tuple(t for t in (_first(entry, "cuisine", "category", "variantName"),) if t),
            ))

        if offers:
            # One line naming what the user is about to be shown. Swiggy menu
            # payloads are reported to carry paise in some fields, and this is
            # how we find out for certain rather than guessing a /100.
            first = offers[0]
            logger.info(
                f"[{self.name}] {len(offers)} offers — first: {first.title!r} "
                f"from {first.venue!r} price={first.price} rating={first.rating} "
                f"eta={first.eta_minutes}"
            )
        if unpriced:
            # A menu item always has a price. If we couldn't read one, OUR key
            # list is wrong — say so loudly instead of shipping blank options.
            sample = pairs[0][0] if pairs else {}
            logger.warning(
                f"[{self.name}] {unpriced}/{len(offers)} dishes had no readable "
                f"price. Item keys: {list(sample.keys())[:15]}"
            )
        if pairs and not offers:
            logger.warning(
                f"[{self.name}] {len(pairs)} entries returned, none orderable "
                f"(missing restaurant/item id). Keys: "
                f"{list(pairs[0][0].keys())[:15]}"
            )
        return offers

    async def place(self, offer: Offer, quantity: int, ctx: SearchContext) -> PlacedOrder:
        client = await self._session()
        address_id = ctx.address_id or await client.default_address_id()
        restaurant_id, item_id = _unpack_id(offer.id)

        if not restaurant_id:
            raise ProviderError(f"offer {offer.id!r} carries no restaurant id")

        cart = await client.add_to_cart(
            address_id=address_id,
            restaurant_id=restaurant_id,
            cart_items=[{"itemId": item_id, "quantity": max(1, quantity)}],
            restaurant_name=offer.venue,
        )
        if getattr(cart, "isError", False):
            logger.error(f"[{self.name}] cart rejected {item_id}: {_error_text(cart)}")
            raise ItemUnavailable(f"cart rejected {item_id}")

        result = await client.place_order(address_id)
        if getattr(result, "isError", False):
            detail = _error_text(result)
            logger.error(f"[{self.name}] checkout failed: {detail}")
            if _is_item_problem(detail):
                raise ItemUnavailable(f"checkout rejected {item_id}: item problem")
            raise ProviderError("order rejected at checkout")

        # isError is False, so Swiggy ACCEPTED the order. An unparseable id is a
        # reporting gap, never grounds to call a placed order failed — doing that
        # sends the user into the retry flow and risks ordering twice.
        payload = mcp.payload_of(result)
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
            eta_minutes=_eta(payload) or offer.eta_minutes,
            total=_num(_first(payload, "cartTotal", "orderTotal", "grandTotal",
                              "total", "amount")) or offer.price,
            items=(offer.title,),
        )

    async def track(self, order_id: str, ctx: SearchContext) -> OrderStatus:
        client = await self._session()
        if not client.supports("order_status"):
            return OrderStatus(order_id=order_id, status=UNKNOWN)

        payload = await client.track(order_id)
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
    """On by default now that the tool surface is verified against the live
    server, and the client re-checks it on every connect.

    Set SWIGGY_FOOD_ENABLED=0 to run grocery-only. The failure mode if a
    response shape is wrong is zero offers — an honest "no results" — never an
    invented restaurant.
    """
    return os.getenv("SWIGGY_FOOD_ENABLED", "1").strip().lower() not in ("0", "false", "no")
