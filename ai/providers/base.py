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
from dataclasses import dataclass, field
from enum import Enum
from typing import Protocol, runtime_checkable


class ProviderKind(str, Enum):
    """What a provider sells. Used to route a request to the right platforms."""
    RESTAURANT = "restaurant"
    GROCERY = "grocery"


@dataclass(frozen=True)
class SearchContext:
    """Everything a provider needs to answer a search, with no user model
    leaking through — providers get requirements, not a person's profile."""
    address_id: str | None = None
    max_price: float | None = None
    limit: int = 10


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
            bits.append(f"{self.currency} {self.price:g}")
        if self.rating is not None:
            bits.append(f"rated {self.rating}")
        if self.eta_minutes is not None:
            bits.append(f"{self.eta_minutes} min")
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
