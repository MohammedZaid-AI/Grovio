"""
Skills — the layer between the planner and the providers.

    planner  →  skills  →  registry  →  oauth/vault  →  provider

A skill answers "can this user do this, and what happened when they tried?" It
returns a `SkillResult` the planner turns into conversation.

WHY THIS EXISTS: the planner must never learn that OAuth is a thing. Without
this layer, the planner would end up checking link status and holding link URLs,
and the credential boundary would erode one convenience at a time. Skills
convert every provider-side reality — missing authorisation, expired grant, no
capability, nothing found — into plain instructions the model can speak.

No skill ever returns a token.
"""
from dataclasses import dataclass, field
from enum import Enum

from core.logger import logger

from ai import recommendation
from ai.providers import ProviderKind, SearchContext, oauth, registry


class SkillStatus(str, Enum):
    OK = "ok"
    NEEDS_LINK = "needs_link"              # user must connect an account
    CAPABILITY_UNAVAILABLE = "unavailable"  # nothing can serve this at all
    EMPTY = "empty"                        # searched, found nothing
    FILTERED = "filtered"                  # found things, all unsuitable
    ERROR = "error"


@dataclass
class SkillResult:
    """What the planner gets back. `message` is written FOR the model — it is
    an instruction about reality, not a reply to the user."""
    status: SkillStatus
    message: str
    link_url: str | None = None
    provider_label: str | None = None
    offers: list = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.status == SkillStatus.OK


def _label(provider_name: str) -> str:
    provider = registry.get(provider_name)
    return getattr(provider, "display_name", None) or provider_name.replace("_", " ").title()


async def _link_prompt(user, provider_name: str, pending_message: str) -> SkillResult:
    """Build a connect prompt for one provider, stashing what the user asked
    for so the conversation can resume after they authorise."""
    provider = registry.get(provider_name)
    config = getattr(provider, "oauth", None)
    label = _label(provider_name)

    if config is None:
        return SkillResult(SkillStatus.ERROR, f"ERROR: {provider_name} is not linkable.")

    try:
        url = await oauth.begin(user.phone, provider_name, config, pending_message)
    except oauth.OAuthError as e:
        logger.error(f"[skills] could not start link for {provider_name}: {e}")
        return SkillResult(
            SkillStatus.ERROR,
            f"ERROR: the {label} connection is temporarily unavailable. Apologise "
            f"briefly and suggest trying again shortly.",
        )

    return SkillResult(
        status=SkillStatus.NEEDS_LINK,
        provider_label=label,
        link_url=url,
        message=(
            f"NEEDS_LINK: this user has not connected their {label} account, so no "
            f"real results exist yet. Tell them warmly that you can take it from "
            f"here once they connect {label}, and include this link EXACTLY as-is "
            f"on its own line: {url}\n"
            f"Mention that you'll pick up right where they left off. Do NOT invent "
            f"any food options in the meantime."
        ),
    )


async def find_food(user, query: str, kind: str, max_price: float | None = None,
                    pending_message: str | None = None) -> SkillResult:
    """Search real, orderable food and rank it for this user.

    The only route to provider data. Returns NEEDS_LINK rather than failing when
    the user hasn't authorised the platform that would serve the request.
    """
    try:
        provider_kind = ProviderKind(kind)
    except ValueError:
        return SkillResult(SkillStatus.ERROR, f"ERROR: unknown kind {kind!r}. Use 'restaurant' or 'grocery'.")

    if not registry.supports(provider_kind):
        # Structural anti-hallucination guard: no provider means no data.
        return SkillResult(
            SkillStatus.CAPABILITY_UNAVAILABLE,
            f"CAPABILITY_UNAVAILABLE: no {provider_kind.value} provider is connected, "
            f"so there are no real results. Tell the user plainly that you cannot "
            f"search {provider_kind.value}s yet. Do NOT invent any.",
        )

    # Anything needing authorisation this user hasn't granted? Ask for it first —
    # searching would silently return a partial catalogue.
    gaps = await registry.link_gaps(user.phone, provider_kind)
    if gaps:
        return await _link_prompt(user, gaps[0], pending_message or query)

    offers = await registry.search(
        provider_kind, query, SearchContext(max_price=max_price, limit=20), phone=user.phone
    )
    if not offers:
        return SkillResult(
            SkillStatus.EMPTY,
            f"No {provider_kind.value} results for {query!r}. Suggest they try something else.",
        )

    ranked = recommendation.rank(offers, user.profile)
    if not ranked:
        return SkillResult(
            SkillStatus.FILTERED,
            f"Every result for {query!r} was filtered out by their allergies or "
            f"dislikes. Say so and offer an alternative.",
        )

    lines = [f"{i}. {rec.explain()}" for i, rec in enumerate(ranked, 1)]
    return SkillResult(
        status=SkillStatus.OK,
        offers=ranked,
        message="Real options, best first. Use ONLY these, with their stated reasons:\n"
                + "\n".join(lines),
    )


async def connect_provider(user, provider_name: str | None = None,
                           pending_message: str | None = None) -> SkillResult:
    """Explicitly start (or restart) a link — for "connect my Swiggy" and for
    reconnecting after a revoked grant."""
    candidates = [
        p.name for p in _linkable_providers()
        if provider_name is None or p.name == provider_name
        or provider_name.lower() in p.name.lower()
    ]
    if not candidates:
        return SkillResult(
            SkillStatus.ERROR,
            f"ERROR: no linkable provider matches {provider_name!r}. Tell the user "
            f"which accounts you support: {', '.join(_linkable_names()) or 'none yet'}.",
        )
    return await _link_prompt(user, candidates[0], pending_message or "")


def disconnect_provider(user, provider_name: str) -> SkillResult:
    """Forget a user's credentials for one provider."""
    from ai.providers import vault

    match = next(
        (p.name for p in _linkable_providers()
         if p.name == provider_name or provider_name.lower() in p.name.lower()),
        None,
    )
    if not match:
        return SkillResult(SkillStatus.ERROR, f"ERROR: no linked account matches {provider_name!r}.")

    vault.unlink(user.phone, match)
    return SkillResult(
        SkillStatus.OK,
        f"Disconnected {_label(match)}. Confirm it's done and mention they can "
        f"reconnect any time.",
    )


def _linkable_providers():
    seen, out = set(), []
    for kind in registry.available_kinds():
        for provider in registry.for_kind(kind):
            if registry.requires_link(provider) and provider.name not in seen:
                seen.add(provider.name)
                out.append(provider)
    return out


def _linkable_names():
    return [_label(p.name) for p in _linkable_providers()]
