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
_by_name: dict[str, Provider] = {}


def register(provider: Provider) -> None:
    _providers.setdefault(provider.kind, []).append(provider)
    _by_name[provider.name] = provider
    logger.info(f"[providers] registered {provider.name} ({provider.kind.value})")


def clear() -> None:
    """Reset the registry (tests)."""
    _providers.clear()
    _by_name.clear()


def get(name: str):
    """Look up a registered provider by name.

    For the OAuth callback route, which receives a provider name in the URL and
    must resolve its config. Business logic routes by CAPABILITY, never by name.
    """
    return _by_name.get(name)


def oauth_config_for(name: str):
    """The OAuthConfig of a registered provider, or None. Lets the OAuth engine
    resolve a provider without importing one."""
    provider = _by_name.get(name)
    return getattr(provider, "oauth", None)


def requires_link(provider) -> bool:
    return getattr(provider, "oauth", None) is not None


def for_kind(kind: ProviderKind) -> list[Provider]:
    return list(_providers.get(kind, []))


def available_kinds() -> list[ProviderKind]:
    return [kind for kind, providers in _providers.items() if providers]


def supports(kind: ProviderKind) -> bool:
    return bool(_providers.get(kind))


async def link_gaps(phone: str, kind: ProviderKind) -> list:
    """Which providers of this kind this user has not authorised.

    Lets the skills layer prompt for a link WITHOUT ever touching a credential —
    it learns only that a connection is missing.
    """
    from ai.providers import vault

    gaps = []
    for provider in for_kind(kind):
        if requires_link(provider) and not vault.is_linked(phone, provider.name):
            gaps.append(provider.name)
    return gaps


async def _context_for(provider, ctx: SearchContext, phone: str | None):
    """Attach this user's credential to the context, or signal that we can't.

    Returns (context, None) when the provider is usable, or (None, reason) when
    the user must link first.
    """
    from ai.providers import vault

    if not requires_link(provider):
        return ctx, None
    if not phone:
        return None, "no_user"

    try:
        token = await vault.access_token(phone, provider.name, provider.oauth)
    except vault.NeedsLink as e:
        return None, e.reason
    return ctx.with_token(token), None


async def search(kind: ProviderKind, query: str, ctx: SearchContext | None = None,
                 phone: str | None = None) -> tuple:
    """Fan out across every provider of this kind and merge the results.

    Returns `(offers, errors)`. Providers requiring authorisation this user
    hasn't granted are skipped silently — `link_gaps()` is how a caller learns
    to prompt for them. A provider that fails is logged and skipped: one broken
    platform must never take down the conversation.

    The errors come back rather than being swallowed so the caller can tell
    "nobody sells this" from "the search itself broke". Reporting the second as
    the first is how a configuration failure became "no results", and how any
    failure became "the provider is down".
    """
    providers = for_kind(kind)
    if not providers:
        return [], []

    ctx = ctx or SearchContext()

    usable, contexts = [], []
    for provider in providers:
        provider_ctx, reason = await _context_for(provider, ctx, phone)
        if provider_ctx is None:
            logger.info(f"[providers] skipping {provider.name}: {reason}")
            continue
        usable.append(provider)
        contexts.append(provider_ctx)

    if not usable:
        return [], []

    results = await asyncio.gather(
        *(p.search(query, c) for p, c in zip(usable, contexts)), return_exceptions=True
    )

    offers: list[Offer] = []
    errors: list[BaseException] = []
    for provider, result in zip(usable, results):
        if isinstance(result, BaseException):
            logger.error(f"[providers] {provider.name} search failed: {result!r}")
            errors.append(result)
            continue
        offers.extend(result)
    return offers, errors
