"""
Provider registry.

The only way the rest of the system reaches a platform. Callers ask for a
*kind* ("who can find restaurant food?"), never for a named platform.

A kind with no registered provider is a first-class, honest state: `search`
returns no offers and says the capability is unavailable. That is what stops the
concierge inventing restaurants when nothing can actually search for them.
"""
import asyncio

from core.logger import logger

from ai.providers.base import Offer, Provider, ProviderKind, SearchContext

_providers: dict[ProviderKind, list[Provider]] = {}


def register(provider: Provider) -> None:
    _providers.setdefault(provider.kind, []).append(provider)
    logger.info(f"[providers] registered {provider.name} ({provider.kind.value})")


def clear() -> None:
    """Reset the registry (tests)."""
    _providers.clear()


def for_kind(kind: ProviderKind) -> list[Provider]:
    return list(_providers.get(kind, []))


def available_kinds() -> list[ProviderKind]:
    return [kind for kind, providers in _providers.items() if providers]


def supports(kind: ProviderKind) -> bool:
    return bool(_providers.get(kind))


async def search(kind: ProviderKind, query: str, ctx: SearchContext | None = None) -> list[Offer]:
    """Fan out across every provider of this kind and merge the results.

    A provider that fails is logged and skipped — one broken platform must never
    take down the conversation. Callers distinguish "nothing found" from "cannot
    search" via `supports()`.
    """
    providers = for_kind(kind)
    if not providers:
        return []

    ctx = ctx or SearchContext()
    results = await asyncio.gather(
        *(p.search(query, ctx) for p in providers), return_exceptions=True
    )

    offers: list[Offer] = []
    for provider, result in zip(providers, results):
        if isinstance(result, BaseException):
            logger.error(f"[providers] {provider.name} search failed: {result!r}")
            continue
        offers.extend(result)
    return offers
