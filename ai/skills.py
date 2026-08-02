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
import json
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from enum import Enum

import db
from core.logger import logger

from ai import recommendation
from ai.providers import ProviderKind, SearchContext, base, oauth, registry

# How long a shown list stays orderable. Long enough to think, short enough that
# "the second one" can't mean yesterday's list.
OFFER_TTL_MINUTES = 45


class SkillStatus(str, Enum):
    OK = "ok"
    NEEDS_LINK = "needs_link"              # user must connect an account
    CAPABILITY_UNAVAILABLE = "unavailable"  # nothing can serve this at all
    EMPTY = "empty"                        # searched, found nothing
    FILTERED = "filtered"                  # found things, all unsuitable
    STALE = "stale"                        # the offered list expired
    UNAVAILABLE_ITEM = "item_unavailable"  # chosen thing can't be ordered now
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

    _remember_offers(user.phone, ranked, query)

    lines = [f"{i}. {rec.explain()}" for i, rec in enumerate(ranked, 1)]
    return SkillResult(
        status=SkillStatus.OK,
        offers=ranked,
        message=(
            "Real options, best first — numbered as the user will see them. Use ONLY "
            "these, with their stated reasons. To order, call place_order with the "
            "number:\n" + "\n".join(lines)
        ),
    )


# ----------------------------------------------------------------------
# Ordering
# ----------------------------------------------------------------------
def _remember_offers(phone: str, ranked: list, query: str) -> None:
    """Persist exactly what was shown, in order.

    SAFETY: this is what makes "order the second one" resolve to a REAL offer.
    The model picks an index into this list; it can never name its way into
    ordering something that was never offered.
    """
    payload = [
        {
            "provider": rec.offer.provider,
            "id": rec.offer.id,
            "title": rec.offer.title,
            "venue": rec.offer.venue,
            "price": rec.offer.price,
            "currency": rec.offer.currency,
            "eta_minutes": rec.offer.eta_minutes,
            "kind": rec.offer.kind.value,
        }
        for rec in ranked
    ]
    db.save_offer_session(phone, json.dumps(payload), query)


def _recall_offers(phone: str):
    """The offers still in play, or None if there are none or they went stale."""
    session = db.get_offer_session(phone)
    if not session:
        return None
    try:
        created = datetime.strptime(str(session["created_at"])[:19], "%Y-%m-%d %H:%M:%S")
    except ValueError:
        return None
    if created + timedelta(minutes=OFFER_TTL_MINUTES) < datetime.now(timezone.utc).replace(tzinfo=None):
        return None
    try:
        return json.loads(session["offers"])
    except (TypeError, ValueError):
        return None


def _offer_from(entry: dict):
    return base.Offer(
        provider=entry["provider"],
        kind=ProviderKind(entry["kind"]),
        id=entry["id"],
        title=entry["title"],
        venue=entry.get("venue"),
        price=entry.get("price"),
        currency=entry.get("currency") or "INR",
        eta_minutes=entry.get("eta_minutes"),
    )


async def place_order(user, selection: int, quantity: int = 1) -> SkillResult:
    """Order one of the options we just showed, by its number.

    Takes an INDEX, never a name: the model cannot conjure an item that was
    never offered, which is the money-spending equivalent of not inventing a
    restaurant.
    """
    offers = _recall_offers(user.phone)
    if not offers:
        return SkillResult(
            SkillStatus.STALE,
            "No options are currently on the table (nothing shown recently, or it "
            "expired). Search again with find_food before ordering, and tell the "
            "user you're refreshing the options.",
        )

    try:
        index = int(selection) - 1
    except (TypeError, ValueError):
        index = -1
    if index < 0 or index >= len(offers):
        return SkillResult(
            SkillStatus.ERROR,
            f"ERROR: there is no option {selection}. Only 1–{len(offers)} were shown. "
            f"Ask which one they meant.",
        )

    entry = offers[index]
    offer = _offer_from(entry)
    provider = registry.get(offer.provider)

    if provider is None or not hasattr(provider, "place"):
        return SkillResult(
            SkillStatus.CAPABILITY_UNAVAILABLE,
            "CAPABILITY_UNAVAILABLE: ordering isn't available for this option yet. "
            "Say so plainly and offer to keep helping them decide. Do NOT claim an "
            "order was placed.",
        )

    ctx, reason = await _ordering_context(user, provider)
    if ctx is None:
        return await _link_prompt(user, provider.name, f"order option {selection}")

    try:
        placed = await provider.place(offer, max(1, int(quantity or 1)), ctx)
    except base.ItemUnavailable:
        return SkillResult(
            SkillStatus.UNAVAILABLE_ITEM,
            f"'{offer.title}' can't be ordered right now — it's unavailable or the "
            f"store is closed. Say so, and offer the other options already shown.",
        )
    except Exception as e:
        logger.error(f"[skills] order failed on {offer.provider}: {e!r}", exc_info=True)
        return SkillResult(
            SkillStatus.ERROR,
            "ERROR: the order could not be placed. Apologise briefly, say nothing was "
            "charged, and offer to try again. Do NOT expose technical details and do "
            "NOT claim it succeeded.",
        )

    order_id = db.save_order(
        phone=user.phone,
        provider=placed.provider,
        provider_order_id=placed.order_id,
        status=placed.status,
        title=offer.title,
        venue=offer.venue,
        total=placed.total if placed.total is not None else offer.price,
        currency=placed.currency,
        eta_minutes=placed.eta_minutes,
    )
    db.clear_offer_session(user.phone)

    from ai import memory
    memory.remember_food(user.phone, offer.title, memory.ORDERED, offer.venue)

    eta = f"about {placed.eta_minutes} minutes" if placed.eta_minutes is not None \
        else "no ETA available yet"
    total = f"{placed.currency} {placed.total:g}" if placed.total is not None else "amount not returned"
    return SkillResult(
        SkillStatus.OK,
        f"ORDER PLACED. {offer.title}"
        + (f" from {offer.venue}" if offer.venue else "")
        + f". Total: {total}. ETA: {eta}. Internal reference #{order_id}.\n"
        f"Confirm it warmly in one or two lines, state the ETA if there is one, and "
        f"mention they can ask where it is any time. Do NOT invent a delivery time.",
        offers=[offer],
    )


async def check_order(user) -> SkillResult:
    """Answer 'where's my order?' from real provider state where possible."""
    order = db.get_active_order(user.phone) or db.get_latest_order(user.phone)
    if not order:
        return SkillResult(
            SkillStatus.EMPTY,
            "This user has no orders. Say so and offer to find them something.",
        )

    known = (
        f"{order['title']}" + (f" from {order['venue']}" if order["venue"] else "")
        + f", ordered {order['placed_at']}, last known status "
        f"{base.STATUS_PHRASING.get(order['status'], 'in progress')}"
    )

    provider = registry.get(order["provider"])
    if provider is None or not getattr(provider, "supports_tracking", False):
        eta = f" Original ETA was about {order['eta_minutes']} minutes." if order["eta_minutes"] else ""
        return SkillResult(
            SkillStatus.CAPABILITY_UNAVAILABLE,
            f"NO LIVE TRACKING for this order: {known}.{eta} Tell them honestly that "
            f"you can't see live status for this one, share what you do know, and "
            f"suggest the provider's own app for minute-by-minute updates. Do NOT "
            f"invent a status or a countdown.",
        )

    ctx, _ = await _ordering_context(user, provider)
    if ctx is None:
        return await _link_prompt(user, provider.name, "where's my order")

    try:
        status = await provider.track(order["provider_order_id"], ctx)
    except Exception as e:
        logger.error(f"[skills] tracking failed: {e!r}")
        return SkillResult(
            SkillStatus.ERROR,
            f"Live status is temporarily unavailable. Share what's known: {known}. "
            f"Apologise briefly, suggest checking again shortly.",
        )

    db.update_order_status(order["id"], status.status, status.eta_minutes)
    eta = f" ETA about {status.eta_minutes} minutes." if status.eta_minutes is not None else ""
    return SkillResult(
        SkillStatus.OK,
        f"Order status: {order['title']} is {status.phrasing}.{eta}"
        + (f" {status.note}" if status.note else "")
        + "\nRelay this naturally in one line.",
    )


async def cancel_order(user) -> SkillResult:
    order = db.get_active_order(user.phone)
    if not order:
        return SkillResult(
            SkillStatus.EMPTY,
            "There's no active order to cancel. Say so gently.",
        )

    provider = registry.get(order["provider"])
    if provider is None or not getattr(provider, "supports_cancellation", False):
        return SkillResult(
            SkillStatus.CAPABILITY_UNAVAILABLE,
            f"CANCELLATION NOT SUPPORTED here. Tell them you can't cancel from this "
            f"chat, and that they'll need the provider's own app or support to stop "
            f"order '{order['title']}'. Do NOT claim it was cancelled.",
        )

    ctx, _ = await _ordering_context(user, provider)
    if ctx is None:
        return await _link_prompt(user, provider.name, "cancel my order")

    try:
        cancelled = await provider.cancel(order["provider_order_id"], ctx)
    except Exception as e:
        logger.error(f"[skills] cancellation failed: {e!r}")
        cancelled = False

    if not cancelled:
        return SkillResult(
            SkillStatus.ERROR,
            "The order could not be cancelled — it may already be on its way. Say so "
            "honestly and suggest contacting the provider directly.",
        )

    db.update_order_status(order["id"], base.CANCELLED)
    return SkillResult(SkillStatus.OK, "Order cancelled. Confirm briefly and kindly.")


async def _ordering_context(user, provider):
    """Credentials for an ordering call, or (None, reason) if the user must link."""
    from ai.providers.registry import _context_for

    return await _context_for(provider, SearchContext(), user.phone)


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
