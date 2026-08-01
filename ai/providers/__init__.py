"""
Provider layer.

Import from here, never from a platform module directly:

    from ai.providers import ProviderKind, registry
    offers = await registry.search(ProviderKind.GROCERY, "milk")

`setup()` wires the concrete platforms. It is the single place that decides
which providers exist, so the rest of the system stays free of platform names.
"""
from ai.providers import registry
from ai.providers.base import Offer, Provider, ProviderKind, SearchContext

__all__ = ["Offer", "Provider", "ProviderKind", "SearchContext", "registry", "setup"]


def setup() -> None:
    """Register the available platforms. Called once at startup.

    RESTAURANT is deliberately unregistered: no restaurant data source is wired
    up yet, so the concierge will say it cannot search rather than invent
    venues. Registering a stub here would be the hallucination bug.
    """
    from ai.providers.swiggy import SwiggyInstamartProvider

    registry.register(SwiggyInstamartProvider())
