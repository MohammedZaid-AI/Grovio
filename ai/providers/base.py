"""
Provider abstraction.

Everything above this layer is provider-agnostic. Restaurant platforms, grocery
platforms and anything added later (pharmacy, meal kits) implement the same
`Provider` protocol and are reached only through the registry.

The rule that keeps this honest: **no module outside ai/providers/ may import a
provider implementation or mention a platform by name.**

`Offer` is the neutral currency. Every field is either real data returned by a
provider or None — there are no defaults that could be mistaken for facts. A
rating of None means "unknown", never "unrated", and the recommendation layer
must not invent one.
"""
from dataclasses import dataclass, field, replace
from enum import Enum
from typing import Protocol, runtime_checkable


class ProviderKind(str, Enum):
    """What a provider sells. Used to route a request to the right platforms."""
    RESTAURANT = "restaurant"
    GROCERY = "grocery"


@dataclass(frozen=True)
class SearchContext:
    """Everything a provider needs to answer a search, with no user model
    leaking through — providers get requirements, not a person's profile.

    `access_token` is populated by the registry for providers that act on a
    user's behalf. It is the ONLY credential a provider ever sees, and the
    provider never learns whose it is.
    """
    address_id: str | None = None
    max_price: float | None = None
    limit: int = 10
    access_token: str | None = None

    def with_token(self, token: str | None) -> "SearchContext":
        return replace(self, access_token=token)


@dataclass(frozen=True)
class Offer:
    """One orderable thing. None means unknown — never assume a value."""
    provider: str
    kind: ProviderKind
    id: str
    title: str
    venue: str | None = None
    price: float | None = None
    currency: str = "INR"
    rating: float | None = None
    eta_minutes: int | None = None
    distance_km: float | None = None
    url: str | None = None
    available: bool = True
    tags: tuple = field(default_factory=tuple)

    def summary(self) -> str:
        """A factual one-liner. Only states fields that actually have values."""
        bits = [self.title]
        if self.venue:
            bits.append(f"from {self.venue}")
        if self.price is not None:
            symbol = "₹" if self.currency == "INR" else f"{self.currency} "
            bits.append(f"{symbol}{self.price:g}")
        if self.rating is not None:
            bits.append(f"rated {self.rating}")
        if self.eta_minutes is not None:
            bits.append(f"{self.eta_minutes} min")
        # Pack size for groceries, cuisine for restaurants. Shown because a
        # grocery option without its pack size is not a choosable thing.
        bits.extend(str(tag) for tag in self.tags if tag)
        return " · ".join(bits)


@runtime_checkable
class Provider(Protocol):
    """Implement this to plug in a platform.

    Ordering (`place`, `track`) joins this protocol in Phase 6, when there is a
    real order to place. Declaring those methods now would be dead surface.
    """
    name: str
    kind: ProviderKind

    async def search(self, query: str, ctx: SearchContext) -> list[Offer]:
        ...


# Order lifecycle, normalised across platforms. A provider maps its own
# vocabulary onto these; nothing above the provider layer sees platform states.
PLACED = "PLACED"
# The provider created the order but is waiting for the user to pay. NOT placed:
# nothing is cooking and nothing is owed until payment lands. Kept distinct so
# no layer can mistake "we asked for money" for "the food is coming".
PENDING_PAYMENT = "PENDING_PAYMENT"
CONFIRMED = "CONFIRMED"
PREPARING = "PREPARING"
ON_THE_WAY = "ON_THE_WAY"
DELIVERED = "DELIVERED"
CANCELLED = "CANCELLED"
UNKNOWN = "UNKNOWN"

# What to say for each state. Kept here so one platform's wording cannot leak
# upward, and so the model is never left to invent a status.
STATUS_PHRASING = {
    PLACED: "placed",
    PENDING_PAYMENT: "waiting for payment",
    CONFIRMED: "confirmed by the restaurant",
    PREPARING: "being prepared",
    ON_THE_WAY: "out for delivery",
    DELIVERED: "delivered",
    CANCELLED: "cancelled",
    UNKNOWN: "in progress",
}


@dataclass(frozen=True)
class PlacedOrder:
    """The result of spending someone's money. Every field is real or None."""
    provider: str
    order_id: str
    status: str = PLACED
    eta_minutes: int | None = None
    total: float | None = None
    currency: str = "INR"
    items: tuple = field(default_factory=tuple)
    note: str | None = None

    # Set only when `status == PENDING_PAYMENT`. `payment_url` is the provider's
    # own hosted payment page — the ONLY thing we hand the user, so no card or
    # UPI detail ever passes through us. `payment_ref` and the poll timings come
    # straight from the provider; nothing here is a guess.
    payment_url: str | None = None
    payment_ref: str | None = None
    poll_interval_ms: int | None = None
    poll_timeout_ms: int | None = None

    @property
    def needs_payment(self) -> bool:
        return self.status == PENDING_PAYMENT


@dataclass(frozen=True)
class OrderStatus:
    """Live state of an order. `eta_minutes` is None when genuinely unknown —
    never a guess, because a wrong ETA is worse than no ETA."""
    order_id: str
    status: str = UNKNOWN
    eta_minutes: int | None = None
    note: str | None = None

    @property
    def phrasing(self) -> str:
        return STATUS_PHRASING.get(self.status, STATUS_PHRASING[UNKNOWN])


class ProviderError(Exception):
    """A provider could not complete an operation. Message is for logs only —
    the user never sees a provider's raw words."""


class ItemUnavailable(ProviderError):
    """The chosen item cannot be ordered right now (sold out, kitchen closed)."""


@runtime_checkable
class OrderingProvider(Provider, Protocol):
    """A provider that can actually place an order.

    `track` and `cancel` are declared but capability-gated: a platform that
    exposes no tracking sets `supports_tracking = False` and the layers above
    say so honestly rather than inventing a status.
    """
    supports_tracking: bool
    supports_cancellation: bool

    async def place(self, offer: Offer, quantity: int, ctx: SearchContext) -> PlacedOrder:
        ...


@runtime_checkable
class LinkableProvider(Provider, Protocol):
    """A provider that acts on behalf of a specific user and therefore needs
    that user's authorisation.

    A provider declares `oauth` (an OAuthConfig) and nothing else — the flow,
    storage and refresh are handled generically. Providers that need no
    per-user credentials simply omit this and stay plain `Provider`s.
    """
    oauth: object          # ai.providers.oauth.OAuthConfig
    display_name: str      # what to call it when asking the user to connect
