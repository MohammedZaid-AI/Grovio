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

    _report_redirect_uris()


def _report_redirect_uris() -> None:
    """Print each provider's callback URL at startup.

    Providers allowlist redirect URIs by EXACT match, so linking fails at their
    consent screen — not in our logs — if the URL is off by a character or the
    host was never registered. Printing it is the difference between "linking
    doesn't work" and a string you can paste into a whitelisting request.
    """
    from ai.providers import oauth

    for name in sorted({p.name for kind in registry.available_kinds()
                        for p in registry.for_kind(kind) if registry.requires_link(p)}):
        logger.info(f"[providers] {name} redirect URI: {oauth.redirect_uri(name)}")

    base = oauth.redirect_uri("_").rsplit("/link/", 1)[0]
    if any(marker in base for marker in ("ngrok-free", "trycloudflare", "loca.lt", "serveo")):
        logger.warning(
            "[providers] PUBLIC_BASE_URL is an EPHEMERAL TUNNEL. Providers match "
            "redirect URIs exactly, and this host changes every restart — so a "
            "whitelisted link breaks as soon as you restart the tunnel. Use a "
            "reserved/static domain before asking anyone to allowlist it."
        )
    elif base.startswith("http://localhost"):
        logger.info(
            "[providers] PUBLIC_BASE_URL is localhost — fine for development, but "
            "the callback only works in a browser ON THIS MACHINE."
        )
