"""
Provider layer.

Import from here, never from a platform module directly:

    from ai.providers import ProviderKind, registry
    offers, errors = await registry.search(ProviderKind.GROCERY, "milk")

`setup()` wires the concrete platforms. It is the single place that decides
which providers exist, so the rest of the system stays free of platform names.
"""
from core.logger import logger

from ai.providers import registry
from ai.providers.base import Offer, Provider, ProviderKind, SearchContext

__all__ = ["Offer", "Provider", "ProviderKind", "SearchContext", "registry", "setup"]


def setup() -> None:
    """Register the available platforms. Called once at startup.

    The restaurant provider is opt-in via SWIGGY_FOOD_ENABLED because the tool
    names it calls are unverified. While it is off, restaurant search reports
    itself unavailable and the concierge says so honestly — which is correct.
    Registering a stub that returned invented venues would be the worst possible
    change to this codebase.
    """
    from ai.providers.swiggy import SwiggyInstamartProvider
    from ai.providers import swiggy_food

    registry.register(SwiggyInstamartProvider())

    if swiggy_food.enabled():
        registry.register(swiggy_food.SwiggyFoodProvider())
    else:
        logger.info(
            "[providers] restaurant search disabled — set SWIGGY_FOOD_ENABLED=1 "
            "after confirming tool names with inspect_tools.py"
        )
