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
import re

from core.logger import logger

from ai.providers.base import (
    PENDING_PAYMENT,
    PLACED,
    Coupon,
    ItemUnavailable,
    PastOrder,
    Offer,
    OrderStatus,
    PlacedOrder,
    ProviderError,
    ProviderKind,
    SearchContext,
    UNKNOWN,
)
from ai.providers import swiggy
from ai.providers.failures import Failure
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


# The platform refusing our payment method is not the item's fault and not the
# user's. Observed live: "To minimise contact between you and the delivery
# partner, cash option is temporarily disabled." Retrying repeats it forever.
_PAYMENT_PROBLEMS = (
    "cash option", "cash on delivery", "payment method", "payment option",
    "payment mode", "cod is", "cod not",
)


def _is_payment_problem(text: str) -> bool:
    lowered = (text or "").lower()
    return any(needle in lowered for needle in _PAYMENT_PROBLEMS)


# The ONLY values place_food_order accepts. Swiggy says so itself when you send
# anything else: 'Unsupported payment method "gpay://upi/". Use "UPI" with
# intentApp/generateUPIQR for UPI payments, or "Cash" for cash on delivery.'
#
# The picker lists APPS — Google Pay, PhonePe, BHIM — and each app's id
# ("gpay://upi/") is the intentApp, NOT the method. Passing that id through as
# the method is exactly the mistake that error is describing.
UPI = "UPI"
CASH = "Cash"


def _payment_method_of(entry: dict):
    """(method, intent_app, qr) for one picker entry, or None if unusable."""
    declared = str(_first(entry, "paymentMethod", "group", "type") or "").upper()
    if declared in ("CASH", "COD"):
        return CASH, None, False

    if bool(entry.get("generateUPIQR")):
        return UPI, None, True

    intent = _first(entry, "intentApp", "id")
    label = str(_first(entry, "displayName", "name", "label") or "")

    # The QR option is not an app. Observed live with BOTH shapes — once as
    # {"displayName": "Pay with QR", "generateUPIQR": true} and once as
    # {"id": "PayWithQR"} with no flag at all. An intentApp is a launchable URI
    # scheme ("gpay://upi/"); anything calling itself QR is the QR option, and
    # sending "PayWithQR" as an app to open would be the same class of mistake
    # as sending "gpay://upi/" as a payment method.
    if "qr" in f"{intent} {label}".lower():
        return UPI, None, True

    if declared == "UPI" or intent:
        return UPI, str(intent) if intent else None, False
    return None


def _payment_methods(payload: dict) -> list:
    """Flatten the payment picker into [{label, method, intent_app, qr}].

    Swiggy publishes `allMethods` explicitly "for headless clients", which is
    what a chat window is. Mobile/desktop lists are read as a fallback, and cash
    only appears if THIS cart still offers it — it is switched off per cart.

    Anything that does not resolve to UPI or Cash is DROPPED rather than sent
    and rejected at checkout.
    """
    if not isinstance(payload, dict):
        return []
    methods = []

    def add(label, resolved):
        if resolved:
            method, intent_app, qr = resolved
            methods.append({"label": str(label or method), "method": method,
                            "intent_app": intent_app, "qr": qr})

    for entry in payload.get("allMethods") or []:
        if isinstance(entry, str):
            # A bare string is only usable if it already IS a valid method.
            if entry.strip().upper() in ("UPI", "CASH", "COD"):
                add(entry, (CASH if entry.strip().upper() != "UPI" else UPI, None, False))
        elif isinstance(entry, dict):
            add(_first(entry, "displayName", "name", "label"), _payment_method_of(entry))

    platforms = payload.get("platforms") or {}
    if not methods and isinstance(platforms, dict):
        for entry in ((platforms.get("mobile") or {}).get("methods") or []):
            if isinstance(entry, dict) and entry.get("id"):
                add(_first(entry, "displayName", "name") or entry["id"],
                    (UPI, str(entry["id"]), False))
        for entry in ((platforms.get("desktop") or {}).get("methods") or []):
            if isinstance(entry, dict):
                add(_first(entry, "displayName", "name") or "Scan a QR to pay",
                    (UPI, None, True))

    cod = payload.get("cod")
    if cod:
        label = "Cash on delivery"
        if isinstance(cod, dict):
            label = _first(cod, "displayName", "label") or label
        add(label, (CASH, None, False))
    return methods


def _coupons(payload: dict) -> list:
    """Flatten the offers list into [{code, saving, minimum, label}].

    Shapes vary, so this reads several plausible key names and keeps only
    coupons with a code — an offer we cannot apply is not an offer.
    """
    if not isinstance(payload, dict):
        return []

    entries = mcp.items_of(payload, "coupons", "offers", "items", "results", "data")
    found = []
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        code = _first(entry, "couponCode", "coupon_code", "code", "offerCode")
        if not code:
            continue
        found.append({
            "code": str(code),
            # What it takes off. Unknown stays 0 rather than becoming a guess —
            # this number is only ever used to CHOOSE, never to tell the user
            # what they saved.
            "saving": _num(_first(entry, "maxDiscount", "discountAmount", "savings",
                                  "value", "amount")) or 0.0,
            "minimum": _num(_first(entry, "minCartAmount", "minimumOrder",
                                   "minOrderValue", "minAmount")) or 0.0,
            "label": str(_first(entry, "description", "title", "header",
                                "couponDescription") or code),
        })
    return found


def _best_coupon(coupons: list, cart_total) -> dict | None:
    """The biggest saving this cart actually qualifies for.

    Deterministic, like the ranking: same cart, same coupon, every time.
    Anything with a minimum above the cart total is skipped rather than applied
    and rejected.
    """
    affordable = [
        c for c in coupons
        if not c["minimum"] or cart_total is None or cart_total >= c["minimum"]
    ]
    if not affordable:
        return None
    return sorted(affordable, key=lambda c: (-c["saving"], c["code"]))[0]


def _past_orders(payload: dict) -> list:
    """Read an order history out of JSON, whatever it calls its keys."""
    if not isinstance(payload, dict):
        return []
    found = []
    for entry in mcp.items_of(payload, "orders", "items", "results", "history"):
        if not isinstance(entry, dict):
            continue
        venue = _first(entry, "restaurantName", "restaurant_name", "name")
        restaurant = entry.get("restaurant")
        if not venue and isinstance(restaurant, dict):
            venue = _first(restaurant, "name", "restaurantName")
        when = _first(entry, "orderTime", "order_time", "placedAt", "createdAt", "date")

        # An order is its DISHES. A restaurant name alone still tells us where
        # they eat, so it is kept with no title rather than dropped.
        items = entry.get("items") or entry.get("orderItems") or []
        titles = [
            str(_first(i, "name", "itemName", "title") or "")
            for i in items if isinstance(i, dict)
        ]
        titles = [t for t in titles if t]
        for title in titles or ([str(venue)] if venue else []):
            found.append(PastOrder(title=title, venue=str(venue) if venue else None,
                                   when=str(when) if when else None))
    return found


def _past_orders_from_text(text: str) -> list:
    """Read an order history out of a printed listing.

    Same reason as coupons: this server answers plenty of tools in prose. Lines
    are expected to name a dish and its restaurant, as Swiggy prints carts:

        Chicken Biryani from Meghana Foods
        2 x Bold Chicken Sandwich Burger - Popeyes
    """
    found = []
    for line in (text or "").splitlines():
        cleaned = line.strip().lstrip("-*. ").strip()
        if not cleaned or len(cleaned) < 4:
            continue
        # "Your recent orders:" is a heading and "TO PAY: 251" is a total.
        # Neither is something anybody ate.
        if cleaned.endswith(":") or _TOTAL_LINE.match(cleaned):
            continue
        title, venue = cleaned, None
        for separator in (" from ", " - ", " | ", " @ "):
            if separator in cleaned:
                title, _, venue = cleaned.partition(separator)
                break
        title = _QUANTITY.sub("", title).strip(" .:")
        # Prices, totals and headings are not dishes.
        if not title or _RUPEES.search(title) or title.endswith(":"):
            continue
        found.append(PastOrder(title=title,
                               venue=venue.strip(" .:") if venue else None))
    return found


def _cart_rejected(cart: dict):
    """Swiggy's own verdict on the cart it just built, or None if it's fine.

    `update_food_cart` answers isError=False and prints a tidy cart summary even
    when the cart is unusable — observed live:

        statusCode 1 · successful False · errorCodes ['INVALID_ADDON']
        "Apologies! one or more items in your cart are no longer available"

    Reading that back is the only warning we get before checkout dies with the
    useless "Some error while creating the order".
    """
    if not isinstance(cart, dict) or not cart:
        return None          # nothing to read is not a verdict
    codes = cart.get("errorCodes")
    if cart.get("successful") is False or _num(cart.get("statusCode")) \
            or (isinstance(codes, list) and codes):
        return str(cart.get("statusMessage") or codes or "cart rejected")[:200]
    return None


def _text_of(result, limit: int = 400) -> str:
    """Whatever a tool actually said, as text. LOGS and PARSING only — never
    shown to a user. Without it a failure is just isError=True, and an
    unparseable success is just [], neither of which anyone can act on."""
    content = getattr(result, "content", None)
    if content and getattr(content[0], "text", None):
        return str(content[0].text)[:limit]
    structured = getattr(result, "structuredContent", None)
    return str(structured)[:limit] if structured else ""


def _error_text(result) -> str:
    return _text_of(result) or "<no error detail returned>"


# A coupon line has to carry BOTH a code-shaped token and a discount, or it is
# just a sentence. Requiring both is what stops "TO PAY" being offered as a
# coupon — inventing a code is the same sin as inventing a restaurant.
_CODE = re.compile(r"\b([A-Z][A-Z0-9]{3,19})\b")
_RUPEES = re.compile(r"₹\s*(\d+)")
_PERCENT = re.compile(r"(\d+)\s*%")
# "2 x Bold Chicken Sandwich Burger" — the count is not part of the dish.
_QUANTITY = re.compile(r"^\s*\d+\s*[xX*]\s*")
# "Item total: 159", "TO PAY: 251" — a label and a number, not a dish.
_TOTAL_LINE = re.compile(r"^[A-Za-z &]+:\s*[₹\d.,]+$")
# "on orders above ₹149" states what the cart must reach, not what comes
# off it. Read separately so it cannot be mistaken for the saving.
_MINIMUM = re.compile(r"(?:above|over|minimum|min\.?)\s*₹\s*(\d+)", re.I)
_NOT_A_CODE = frozenset({
    "COUPON", "COUPONS", "OFFER", "OFFERS", "CODE", "CODES", "APPLY", "APPLIED",
    "DISCOUNT", "TOTAL", "ITEM", "ITEMS", "CART", "DELIVERY", "TAXES", "CHARGES",
    "RESTAURANT", "ORDER", "ORDERS", "ABOVE", "UPTO", "FREE", "SAVE", "FLAT",
    "AVAILABLE", "USING", "WITH", "YOUR", "THIS", "NULL", "NONE", "TRUE", "FALSE",
})


def _coupons_from_text(text: str) -> list:
    """Read a prose coupon listing, one coupon per line.

    Swiggy answers several Food tools with human-readable text rather than
    JSON — `update_food_cart` replies with a printed cart summary — so a
    listing can arrive looking like:

        TRYNEW - Get ₹80 off on orders above ₹149

    Only lines carrying a plausible code AND a stated discount are accepted, so
    a stray "TO PAY" line can never be offered to someone as a coupon.
    """
    found, seen = [], set()
    for line in (text or "").splitlines():
        floor = _MINIMUM.search(line)
        # Take the threshold out before looking for the saving, or "above ₹149"
        # reads as a ₹149 discount.
        rest = line.replace(floor.group(0), " ") if floor else line
        # A rupee figure is the saving; a bare percentage ("20% off up to ₹100")
        # has its real ceiling in rupees on the same line, so rupees win either
        # way. Percent alone is a discount we cannot price — it still counts as a
        # coupon, it just sorts last rather than pretending to a number.
        amount = _RUPEES.search(rest)
        if not amount and not _PERCENT.search(rest):
            continue
        for code in _CODE.findall(line):
            if code in _NOT_A_CODE or code in seen:
                continue
            seen.add(code)
            found.append({
                "code": code,
                "saving": float(amount.group(1)) if amount else 0.0,
                "minimum": float(floor.group(1)) if floor else 0.0,
                "label": line.strip().lstrip("-*. ").strip(),
            })
            break        # one coupon per line; the rest are words about it
    return found


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


# How far to page for more candidates. Each page is one round trip, so this is
# a trade between choice and how long the user waits for a reply.
MAX_EXTRA_PAGES = 2
MIN_CANDIDATES = 20


def _entry_key(entry):
    """What makes a dish the same dish, for de-duplication."""
    if not isinstance(entry, dict):
        return None
    return (
        str(_first(entry, "menu_item_id", "itemId", "menuItemId", "dishId", "id") or ""),
        str(_first(entry, "restaurant_id", "restaurantId", "resId") or ""),
    )


def _merge(payload: dict, extra: list) -> dict:
    """Append another page, skipping anything already present.

    A server may ignore `offset`, or overlap pages, and listing the same pizza
    three times is worse than offering fewer choices.
    """
    if not isinstance(payload, dict):
        return {"items": list(extra)}

    for key in ("items", "dishes", "results", "menuItems", "cards", "restaurants"):
        current = payload.get(key)
        if isinstance(current, list):
            seen = {_entry_key(e) for e in current}
            fresh = [e for e in extra if _entry_key(e) not in seen]
            return {**payload, key: current + fresh}
    return {**payload, "items": list(extra)}


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
    supports_coupons = True
    supports_history = True

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
            payload = await self._widen(client, query, address_id, payload, ctx.limit)
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

    async def coupons(self, offer: Offer, quantity: int, ctx: SearchContext) -> list[Coupon]:
        """What this cart could be discounted with, best saving first.

        Discounts are scoped to a built cart, so this commits the basket — the
        same basket `place` then checks out. Best effort throughout: no coupon
        list is a reason to pay full price, never a reason to lose the order.
        """
        client = await self._session()
        address_id = ctx.address_id or await client.default_address_id()
        await self._build_cart(client, offer, quantity, address_id)

        available = await self._available_coupons(client, address_id)
        affordable = [
            c for c in available
            if not c["minimum"] or offer.price is None or offer.price >= c["minimum"]
        ]
        affordable.sort(key=lambda c: (-c["saving"], c["code"]))
        logger.info(
            f"[{self.name}] coupons for this cart: "
            f"{[(c['code'], c['saving']) for c in affordable]}"
        )
        return [
            Coupon(code=c["code"], label=c["label"],
                   saving=c["saving"] or None, minimum=c["minimum"] or None)
            for c in affordable
        ]

    async def place(self, offer: Offer, quantity: int, ctx: SearchContext,
                    coupon: str | None = None) -> PlacedOrder:
        client = await self._session()
        address_id = ctx.address_id or await client.default_address_id()
        await self._build_cart(client, offer, quantity, address_id)

        # Three-valued, per the protocol: a code is the one the user chose, ""
        # is them declining, and None means nobody was asked — so pick the best
        # for them. Purely additive either way: if anything here fails the order
        # proceeds at full price.
        if coupon:
            applied = coupon if await self._apply_coupon(client, address_id, coupon) else None
        elif coupon is None:
            applied = await self._apply_best_coupon(client, address_id, offer.price)
        else:
            applied = None

        # Ask what THIS cart can be paid with rather than assuming. Hardcoding
        # "Cash" is what made every order fail once Swiggy disabled it.
        chosen = await self._payment_choice(client, address_id)
        logger.info(f"[{self.name}] paying with {chosen}")

        result = await client.place_order(
            address_id,
            payment_method=chosen["method"],
            intent_app=chosen["intent_app"],
            generate_upi_qr=chosen["qr"],
        )
        if getattr(result, "isError", False):
            detail = _error_text(result)
            logger.error(f"[{self.name}] checkout failed: {detail}")
            if _is_payment_problem(detail):
                # Not the item, not the user — the platform will not take the
                # payment method we send. Flagged so the layers above stop
                # offering a retry that cannot possibly work.
                error = ProviderError(f"payment method refused: {detail[:120]}")
                error.failure = Failure.PAYMENT_UNAVAILABLE
                raise error
            if _is_item_problem(detail):
                raise ItemUnavailable(f"checkout rejected {offer.id}: item problem")
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

        # A UPI order arrives PENDING_PAYMENT with a hosted payment page. It is
        # NOT placed — saying so would be claiming food is coming that nobody
        # has paid for.
        bridge_url = str(_first(payload, "bridgeUrl", "bridge_url",
                                "redirectUrl", "paymentUrl") or "")
        if bridge_url:
            logger.info(f"[{self.name}] order {order_id} awaiting payment")
            return PlacedOrder(
                provider=self.name,
                order_id=order_id,
                status=PENDING_PAYMENT,
                # No fallback to offer.price here, unlike a placed order. That
                # number is what the SEARCH listed (₹269 live) and is neither
                # the dish price (₹159) nor what the payment page will charge
                # (₹251) — printing it next to "tap to pay" states a figure
                # nobody is being asked for.
                total=_num(_first(payload, "cartTotal", "orderTotal", "grandTotal",
                                  "total", "amount")),
                items=(offer.title,),
                payment_url=bridge_url,
                payment_ref=str(_first(payload, "paasId", "paas_id") or "") or None,
                note=f"coupon {applied} applied" if applied else None,
                poll_interval_ms=_int(_first(payload, "pollingIntervalInMs")),
                poll_timeout_ms=_int(_first(payload, "maxTimeToPollForInMs")),
            )

        return PlacedOrder(
            provider=self.name,
            order_id=order_id,
            status=PLACED,
            eta_minutes=_eta(payload) or offer.eta_minutes,
            total=_num(_first(payload, "cartTotal", "orderTotal", "grandTotal",
                              "total", "amount")) or offer.price,
            items=(offer.title,),
            note=f"coupon {applied} applied" if applied else None,
        )

    async def history(self, ctx: SearchContext, limit: int = 20) -> list[PastOrder]:
        """What this person has actually ordered before, from their own account.

        This is the single most useful thing we can know about someone, and it
        already exists — they have been ordering for years. Learning it from
        conversation instead would take months and be worse.

        Best effort in every direction: a platform that will not tell us simply
        means we learn the slow way. Nothing here may raise into a search.
        """
        client = await self._session()
        if not client.supports("orders"):
            return []
        try:
            result = await client.past_orders(limit)
        except Exception as e:
            logger.info(f"[{self.name}] could not read order history: {e!r}")
            return []
        if getattr(result, "isError", False):
            logger.info(f"[{self.name}] get_food_orders failed: {_error_text(result)}")
            return []

        past = _past_orders(mcp.payload_of(result))
        if not past:
            body = _text_of(result, 1500)
            past = _past_orders_from_text(body)
            if not past and body:
                logger.info(
                    f"[{self.name}] no history parsed from get_food_orders. "
                    f"Raw reply: {body[:600]!r}"
                )
        logger.info(f"[{self.name}] read {len(past)} past orders")
        return past[:limit]

    async def locality(self, ctx: SearchContext):
        """The AREA they order to — "Attavar, Mangaluru", never the flat number.

        Deliberately not the full address. It goes into the model's prompt to
        make "there is a good place near you" mean something, and a street
        address is not needed for that. Only explicit area fields are read; a
        one-line address string is left alone rather than sliced on a guess.
        """
        client = await self._session()
        try:
            payload = await client.addresses()
        except Exception as e:
            logger.info(f"[{self.name}] could not read addresses: {e!r}")
            return None

        entries = mcp.items_of(payload, "addresses", "items", "results")
        if not entries or not isinstance(entries[0], dict):
            return None
        first = entries[0]
        area = _first(first, "area", "locality", "subLocality", "sub_locality",
                      "areaName", "landmark")
        city = _first(first, "city", "cityName")
        parts = [str(p).strip() for p in (area, city) if p]
        return ", ".join(dict.fromkeys(parts)) or None

    async def _build_cart(self, client, offer: Offer, quantity: int, address_id: str):
        """Put exactly this item in the cart, replacing whatever was there.

        Called by BOTH `coupons` and `place`, so it runs twice for one order.
        The flush is what makes that safe: without it a second add could stack
        quantities and someone gets — and pays for — two dinners. If the flush
        itself fails we carry on, which is precisely the behaviour that shipped
        before the coupon step existed.
        """
        restaurant_id, item_id = _unpack_id(offer.id)
        if not restaurant_id:
            raise ProviderError(f"offer {offer.id!r} carries no restaurant id")

        if client.supports("flush_cart"):
            try:
                await client.call("flush_cart", {"addressId": address_id})
            except Exception as e:
                logger.warning(f"[{self.name}] could not flush the cart first: {e!r}")

        # search_menu returns the id as `menu_item_id`, so the cart is sent both
        # spellings: the tool schema for update_food_cart is not published in the
        # summary listing, and an item the cart silently ignores produces exactly
        # the symptom seen here — the add "succeeds" and checkout then fails with
        # "Some error while creating the order".
        cart_item = {
            "menu_item_id": item_id,
            "itemId": item_id,
            "quantity": max(1, quantity),
        }
        logger.info(
            f"[{self.name}] update_food_cart payload: addressId={address_id!r} "
            f"restaurantId={restaurant_id!r} items={[cart_item]}"
        )
        cart = await client.add_to_cart(
            address_id=address_id,
            restaurant_id=restaurant_id,
            cart_items=[cart_item],
            restaurant_name=offer.venue,
        )
        logger.info(
            f"[{self.name}] update_food_cart isError="
            f"{getattr(cart, 'isError', None)} body={_error_text(cart)}"
        )
        if getattr(cart, "isError", False):
            logger.error(f"[{self.name}] cart rejected {item_id}: {_error_text(cart)}")
            raise ItemUnavailable(f"cart rejected {item_id}")

        # Confirm the item actually landed BEFORE spending. The Instamart
        # provider learned this the hard way: update_cart can report success
        # while adding nothing, and the emptiness only surfaces as an opaque
        # checkout failure.
        #
        # A cart we cannot READ stays advisory — the tool's argument shape is
        # unverified, and a read failure must never block a valid order. A cart
        # Swiggy explicitly REJECTS is a different thing entirely, and stopping
        # here is what turns "something went wrong, shall I try again?" into
        # "that one's unavailable, here are the others".
        try:
            cart_state = mcp.payload_of(await client.call("cart", {"addressId": address_id}))
        except Exception as e:
            logger.info(f"[{self.name}] could not read the cart back: {e!r}")
            return

        logger.info(f"[{self.name}] get_food_cart: {str(cart_state)[:500]}")
        rejected = _cart_rejected(cart_state)
        if rejected:
            logger.error(f"[{self.name}] cart is unusable after adding {item_id}: {rejected}")
            raise ItemUnavailable(f"cart rejected {item_id}: {rejected}")

    async def _widen(self, client, query, address_id, payload, wanted):
        """Pull more pages so ranking has a real field to choose from.

        One page is ten dishes, often several from the same kitchen — nothing to
        pick between, and no chance of offering somewhere slightly further that
        is genuinely better. Extra pages are best effort: a failure keeps
        whatever the first page returned.
        """
        seen = len(_entries(payload))
        offset = seen
        for _ in range(MAX_EXTRA_PAGES):
            if seen >= max(wanted * 3, MIN_CANDIDATES):
                break
            try:
                more = await client.search_dishes(query, address_id, offset=offset)
            except Exception as e:
                logger.info(f"[{self.name}] could not widen the search: {e!r}")
                break

            extra = mcp.items_of(more, "items", "dishes", "results", "menuItems",
                                 "cards", "restaurants")
            if not extra:
                break
            payload = _merge(payload, extra)
            grown = len(_entries(payload))
            if grown <= seen:
                break        # the page was all duplicates; more paging is waste
            offset += len(extra)
            seen = grown

        logger.info(f"[{self.name}] {seen} candidates for {query!r}")
        return payload

    async def _available_coupons(self, client, address_id) -> list:
        """Every coupon this cart qualifies for, read from whichever shape
        Swiggy answered in.

        Returning [] used to be indistinguishable between three very different
        things: the server erroring, the cart genuinely having no offers, and us
        failing to parse a reply we did get. Live logs showed only
        "no coupons offered for this cart" and there was no way to tell which —
        so anything unparsed is logged verbatim here.
        """
        if not (client.supports("coupons") and client.supports("apply_coupon")):
            logger.info(f"[{self.name}] this server exposes no coupon tools")
            return []
        try:
            result = await client.coupons(address_id)
        except Exception as e:
            logger.warning(f"[{self.name}] fetch_food_coupons raised: {e!r}")
            return []

        if getattr(result, "isError", False):
            logger.warning(
                f"[{self.name}] fetch_food_coupons failed: {_error_text(result)}. "
                f"If this is an argument-name error, the call sends only addressId "
                f"— check it against inspect_tools.py."
            )
            return []

        found = _coupons(mcp.payload_of(result))
        if not found:
            body = _text_of(result, 1200)
            found = _coupons_from_text(body)
            if not found and body:
                logger.info(
                    f"[{self.name}] no coupons parsed from fetch_food_coupons. "
                    f"Raw reply: {body[:600]!r}"
                )
        return found

    async def _apply_best_coupon(self, client, address_id, cart_total):
        """Find the best coupon and apply it. Returns its code, or None.

        Entirely best effort. A discount is a bonus, so nothing in here may
        stop an order going through — every failure logs and returns None, and
        the order proceeds at full price.
        """
        available = await self._available_coupons(client, address_id)
        if not available:
            logger.info(f"[{self.name}] no coupons offered for this cart")
            return None
        logger.info(f"[{self.name}] coupons offered: {[c['code'] for c in available]}")

        best = _best_coupon(available, cart_total)
        if not best:
            return None
        return best["code"] if await self._apply_coupon(client, address_id, best["code"]) else None

    async def _apply_coupon(self, client, address_id, code: str) -> bool:
        """Put one code on the cart. False on any failure — never an exception,
        because a discount that won't stick must not cost someone their dinner."""
        if not client.supports("apply_coupon"):
            return False
        try:
            result = await client.apply_coupon(address_id, code)
        except Exception as e:
            logger.warning(
                f"[{self.name}] apply_food_coupon({code}) raised: {e!r}. "
                f"If this is an argument-name error, fix COUPON_CODE_ARG in "
                f"integrations/swiggy/swiggy_food_mcp.py — see its comment."
            )
            return False

        if getattr(result, "isError", False):
            # Commonly the cart no longer qualifies. Not worth troubling the
            # user with; they simply pay full price.
            logger.info(f"[{self.name}] coupon {code} not applied: {_error_text(result)}")
            return False

        logger.info(f"[{self.name}] applied coupon {code}")
        return True

    async def _payment_choice(self, client, address_id: str) -> dict:
        """Pick how to pay, from what the platform actually offers.

        Cash first when available — it needs nothing from the user. Otherwise
        the first UPI method, which yields a payment link they can tap.
        If the picker cannot be read we fall back to cash and let checkout
        speak for itself, rather than refusing to try.
        """
        fallback = {"label": "Cash on delivery", "method": mcp.PAYMENT_METHOD,
                    "intent_app": None, "qr": False}
        if not client.supports("payment_options"):
            return fallback

        try:
            options = _payment_methods(await client.payment_options(address_id))
        except Exception as e:
            logger.info(f"[{self.name}] could not read payment options: {e!r}")
            return fallback

        if not options:
            return fallback
        # Log the method and intentApp, not just the label. A pretty label told
        # us nothing when the method underneath it was wrong.
        logger.info(
            f"[{self.name}] payment options: "
            f"{[(o['label'], o['method'], o['intent_app'], o['qr']) for o in options]}"
        )

        cash = next((o for o in options if o["method"] == mcp.PAYMENT_METHOD), None)
        return cash or options[0]

    async def payment_status(self, order: PlacedOrder, ctx: SearchContext) -> str:
        """One poll of a pending payment. Returns Swiggy's own status word.

        `pending` on any failure to read it: an unreadable poll must never be
        mistaken for a completed payment.
        """
        client = await self._session()
        if not client.supports("payment_status"):
            return "pending"
        address_id = ctx.address_id or await client.default_address_id()
        try:
            payload = await client.payment_status(
                order_id=order.order_id, paas_id=order.payment_ref or "",
                address_id=address_id,
            )
        except Exception as e:
            logger.info(f"[{self.name}] payment poll failed: {e!r}")
            return "pending"
        status = str(_first(payload, "status", "paymentStatus", "state") or "pending")
        logger.info(f"[{self.name}] payment {order.order_id} -> {status}")
        return status.lower()

    async def confirm_payment(self, order: PlacedOrder, ctx: SearchContext) -> bool:
        """Finalise a paid order. Only called once payment actually succeeded."""
        client = await self._session()
        if not client.supports("confirm"):
            return True          # nothing to call; payment success stands
        address_id = ctx.address_id or await client.default_address_id()
        try:
            await client.confirm_order(order.order_id, address_id, order.payment_ref)
            return True
        except Exception as e:
            logger.error(f"[{self.name}] confirm_order failed: {e!r}")
            return False

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
