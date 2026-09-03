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

import db
from core.logger import logger

from ai import conversation, recommendation
from ai.providers import ProviderKind, SearchContext, base, failures, oauth, registry
from ai.providers.failures import Failure
from core import authz


class SkillStatus(str, Enum):
    OK = "ok"
    NEEDS_LINK = "needs_link"              # user must connect an account
    CAPABILITY_UNAVAILABLE = "unavailable"  # nothing can serve this at all
    EMPTY = "empty"                        # searched, found nothing
    FILTERED = "filtered"                  # found things, all unsuitable
    STALE = "stale"                        # the offered list expired
    UNAVAILABLE_ITEM = "item_unavailable"  # chosen thing can't be ordered now
    CONFIGURATION = "configuration"        # OUR setup is incomplete, not theirs
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
    except Exception as e:
        # NOT all the same thing. A missing OAuth endpoint is a configuration
        # mistake of ours that no amount of retrying will fix, and reporting it
        # as "the provider is temporarily unavailable" tells the user something
        # false about a service that is working fine.
        failure, instruction = failures.instruction_for(e)
        logger.error(
            f"[skills] link for {provider_name} failed — classified as "
            f"{failure.value}: {e}",
            exc_info=failure in (Failure.UNKNOWN, Failure.PARSING),
        )
        return SkillResult(
            SkillStatus.CONFIGURATION if failure is Failure.CONFIGURATION else SkillStatus.ERROR,
            f"{instruction} (This concerns their {label} account.)",
            provider_label=label,
        )

    # OUT THE DOOR VERBATIM, not through the model.
    #
    # This URL is ~300 characters of query string, and handing it to the model
    # to retype is how it got mangled: observed live, a local model wrote
    #   ...authorize?response_type=code&redirect_uri=http%3A%2F
    # and stopped mid-parameter. Swiggy answered "client_id and redirect_uri
    # are required", which reads as an OAuth bug and is not one. Worse, the
    # broken link was then stored in conversation history and reproduced on
    # later turns without OAuth running at all.
    #
    # Same durable outbound queue as the payment link in _await_payment, and
    # for the same reason: a credential-bearing URL must arrive byte for byte.
    from ai import payments

    await payments.notify(user.phone, url)
    logger.info(f"[skills] sent the {label} link to {user.phone} "
                f"({len(url)} chars, delivered verbatim)")

    message = (
        f"NEEDS_LINK: this user has not connected their {label} account, so no "
        f"real results exist yet. The connect link has ALREADY been sent to "
        f"them in a separate message. Say ONE short line asking them to tap it "
        f"to connect {label} so you can see real menus and prices. Do NOT "
        f"repeat the link, do NOT write out any URL, and do NOT invent any "
        f"food options in the meantime."
    )
    # A URL in the model's input is a URL the model can retype, badly. This is
    # the whole point of the change, so it is asserted rather than assumed.
    assert "http" not in message, "the OAuth URL must never reach the model"

    return SkillResult(
        status=SkillStatus.NEEDS_LINK,
        provider_label=label,
        # Kept for callers and tests that need the real URL. It is NOT model
        # input — nothing puts link_url into a prompt.
        link_url=url,
        message=message,
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
        #
        # The instruction not to offer account linking matters: connecting an
        # account does NOT enable a capability nobody can serve, and a model that
        # offers it sends the user through a pointless flow that ends in the same
        # refusal. Observed in live testing.
        available = ", ".join(k.value for k in registry.available_kinds()) or "nothing"
        return SkillResult(
            SkillStatus.CAPABILITY_UNAVAILABLE,
            f"CAPABILITY_UNAVAILABLE: nothing can search {provider_kind.value}s — this "
            f"is not a connection problem and connecting an account will NOT fix it. "
            f"Tell the user plainly that you can't do {provider_kind.value}s yet, do "
            f"NOT offer to connect anything, and do NOT invent options. "
            f"You CAN currently help with: {available}. Offer that instead.",
        )

    # Anything needing authorisation this user hasn't granted? Ask for it first —
    # searching would silently return a partial catalogue.
    gaps = await registry.link_gaps(user.phone, provider_kind)
    if gaps:
        return await _link_prompt(user, gaps[0], pending_message or query)

    # First real search after linking: read what they already order. Runs once,
    # before ranking, because it is the difference between "here are five
    # biryanis" and "the Meghana one, like usual".
    if await learn_about(user):
        # Reload, or this very search ranks against the empty profile we had a
        # moment ago — the one search where knowing them matters most.
        from ai import memory
        user.profile = memory.load(user.phone)

    offers, errors = await registry.search(
        provider_kind, query, SearchContext(max_price=max_price, limit=20), phone=user.phone
    )
    if not offers:
        if errors:
            # Nothing came back AND something broke: report what actually broke,
            # rather than "no results", which would read as an empty catalogue.
            failure, instruction = failures.instruction_for(errors[0])
            logger.error(f"[skills] search for {query!r} failed — {failure.value}: {errors[0]!r}")
            return SkillResult(
                SkillStatus.CONFIGURATION if failure is Failure.CONFIGURATION else SkillStatus.ERROR,
                instruction,
            )
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
# Learning who someone is, from their own account
# ----------------------------------------------------------------------
# A linked account already knows what this person eats and where they eat it.
# Asking them to tell us all over again, one conversation at a time, would be
# worse for them and slower for us.
#
# This is NOT an agent. It is one call per provider, parsed deterministically,
# written into the same food memory that conversation writes to. An LLM in this
# loop could only re-derive what the platform already stated, with a chance of
# getting it wrong — and ranking must stay explainable.
IMPORTED = "_history_imported"
AREA = "delivery_area"

# Enough to see a pattern, few enough that one old phase doesn't drown the rest.
HISTORY_LIMIT = 25


async def learn_about(user) -> int:
    """Import order history and delivery area from every linked provider.

    Runs ONCE per user — the marker is an internal fact, hidden from the prompt.
    Entirely best effort: a provider that cannot answer costs nothing, and no
    failure here may stop a search. Returns how many past orders were learned.
    """
    from ai import memory

    if user.facts.get(IMPORTED):
        return 0

    learned = 0
    for provider in _history_providers():
        ctx, _ = await _ordering_context(user, provider)
        if ctx is None:
            continue          # not linked to this one; nothing to read

        try:
            past = await provider.history(ctx, limit=HISTORY_LIMIT)
        except Exception as e:
            logger.info(f"[skills] history unavailable from {provider.name}: {e!r}")
            past = []

        for order in past:
            try:
                memory.remember_food(user.phone, order.title, memory.ORDERED, order.venue)
                learned += 1
            except Exception:
                logger.info(f"[skills] skipped an unreadable past order", exc_info=True)

        if not user.facts.get(AREA):
            try:
                area = await provider.locality(ctx)
            except Exception as e:
                logger.info(f"[skills] area unavailable from {provider.name}: {e!r}")
                area = None
            if area:
                memory.remember_fact(user.phone, AREA, area)
                logger.info(f"[skills] learned delivery area for {user.phone}")

    # Marked even when nothing came back. A user with no order history is a real
    # answer, and re-asking the platform on every single search would be a call
    # per message for no new information.
    memory.remember_fact(user.phone, IMPORTED, "yes")
    logger.info(f"[skills] learned {learned} past orders for {user.phone}")
    return learned


def _history_providers():
    seen, out = set(), []
    for kind in registry.available_kinds():
        for provider in registry.for_kind(kind):
            if getattr(provider, "supports_history", False) and provider.name not in seen:
                seen.add(provider.name)
                out.append(provider)
    return out


# ----------------------------------------------------------------------
# Ordering
# ----------------------------------------------------------------------
def _remember_offers(phone: str, ranked: list, query: str) -> None:
    """Persist exactly what was shown, in order, as conversation state.

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
    conversation.show_offers(phone, payload, query)


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
    state = conversation.load(user.phone)
    if not state.has_offers:
        return SkillResult(
            SkillStatus.STALE,
            "No options are currently on the table (nothing shown recently, or it "
            "expired). Search again with find_food before ordering, and tell the "
            "user you're refreshing the options.",
        )

    entry = state.offer_at(selection) if str(selection).strip().lstrip("-").isdigit() else None
    if entry is None:
        return SkillResult(
            SkillStatus.ERROR,
            f"ERROR: there is no option {selection}. Only 1–{len(state.offers)} were "
            f"shown. Ask which one they meant.",
        )

    pending = conversation.PendingOrder(
        provider=entry["provider"],
        offer_id=entry["id"],
        title=entry["title"],
        kind=entry.get("kind") or ProviderKind.RESTAURANT.value,
        venue=entry.get("venue"),
        price=entry.get("price"),
        currency=entry.get("currency") or "INR",
        eta_minutes=entry.get("eta_minutes"),
        quantity=max(1, int(quantity or 1)),
        selection=int(selection),
    )
    conversation.begin_order(user.phone, pending)
    logger.info(
        f"[skills] selection {selection} -> {entry['title']!r} on "
        f"{entry['provider']} (offer_id={entry['id']!r}, qty={pending.quantity})"
    )

    # Building a cart WRITES to the account owner's linked account, so the money
    # gate has to hold here too, not only at checkout.
    blocked = _may_spend(user, entry.get("title", ""))
    if blocked:
        return blocked

    offering = await _offer_coupons(user, entry, pending.quantity)
    if offering:
        return offering
    return await _execute_pending(user, entry, quantity=pending.quantity)


async def _offer_coupons(user, entry: dict, quantity: int) -> SkillResult | None:
    """Show the discounts on this cart and wait for a choice.

    Returns None when there is nothing to ask about — no coupon capability, no
    coupons, or the lookup failed — and the order proceeds straight to checkout.
    A discount is a bonus; it must never become a step that loses the order.
    """
    offer = _offer_from(entry)
    provider = registry.get(offer.provider)
    if provider is None or not getattr(provider, "supports_coupons", False):
        return None

    ctx, _ = await _ordering_context(user, provider)
    if ctx is None:
        return None      # not linked; _execute_pending raises the link prompt

    try:
        coupons = await provider.coupons(offer, quantity, ctx)
    except Exception as e:
        # Includes the cart being rejected. Left to _execute_pending to hit
        # again and classify properly — this step never speaks for the order.
        logger.info(f"[skills] could not list coupons for {offer.title!r}: {e!r}")
        return None

    if not coupons:
        logger.info(f"[skills] no coupons for {offer.title!r}; ordering at full price")
        return None

    shown = [{"code": c.code, "label": c.label, "saving": c.saving} for c in coupons[:5]]
    conversation.offer_coupons(user.phone, shown)
    lines = [f"{i}. {c['code']} — {c['label']}" for i, c in enumerate(shown, 1)]
    return SkillResult(
        SkillStatus.OK,
        f"COUPONS AVAILABLE for {offer.title}"
        + (f" from {offer.venue}" if offer.venue else "")
        + ". Nothing is ordered or charged yet.\n"
        + "\n".join(lines)
        + "\nList these EXACTLY as given — the codes and wording are Swiggy's, not "
        "yours, so never invent a code or a saving amount. Ask which one they want, "
        "and say they can skip it. Their reply is handled for you; do NOT call any "
        "tool for this and do NOT claim the order is placed.",
    )


async def choose_coupon(user, code: str) -> SkillResult:
    """Apply the discount they chose (or none, for "") and place the order."""
    state = conversation.load(user.phone)
    if not state.awaiting_coupon:
        return SkillResult(
            SkillStatus.STALE,
            "There is no order waiting on a coupon. Ask what they'd like instead.",
        )

    conversation.choose_coupon(user.phone, code)
    logger.info(f"[skills] coupon {code or '<none>'} chosen for {state.pending.title!r}")
    entry = state.offer_at(state.pending.selection or 0) or _entry_from(state.pending)
    return await _execute_pending(user, entry, quantity=state.pending.quantity, coupon=code)


def _entry_from(pending) -> dict:
    """Rebuild the offer dict from a pending order, for when the shown list is
    gone but the choice is still live."""
    return {
        "provider": pending.provider,
        "id": pending.offer_id,
        "title": pending.title,
        "venue": pending.venue,
        "price": pending.price,
        "currency": pending.currency,
        "eta_minutes": pending.eta_minutes,
        "kind": pending.kind,
    }


async def retry_pending_order(user) -> SkillResult:
    """Retry the order already chosen — never a fresh search.

    This is the whole point of the state machine: 'yes' after a failure must
    resolve to THIS order, deterministically, without the model re-deciding.
    """
    state = conversation.load(user.phone)
    if not state.pending:
        return SkillResult(
            SkillStatus.STALE,
            "There is no order waiting to be retried. Ask what they'd like instead.",
        )

    if state.pending.retries_exhausted:
        return _exhausted(user, state)

    conversation.begin_retry(user.phone)
    entry = state.offer_at(state.pending.selection or 0) or _entry_from(state.pending)
    # Same coupon as the original attempt — a retry retries THIS order, discount
    # included, rather than asking them to choose all over again.
    return await _execute_pending(user, entry, quantity=state.pending.quantity,
                                  coupon=state.pending.coupon)


def _exhausted(user, state) -> SkillResult:
    conversation.give_up(user.phone)
    alternatives = [
        f"{i}. {o['title']}" + (f" from {o['venue']}" if o.get("venue") else "")
        for i, o in enumerate(state.offers, 1)
        if o["id"] != state.pending.offer_id
    ][:3]
    listing = ("\nThe other options still available:\n" + "\n".join(alternatives)) if alternatives else ""
    return SkillResult(
        SkillStatus.ERROR,
        f"RETRY LIMIT REACHED for '{state.pending.title}' — it failed "
        f"{state.pending.retry_count + 1} times. Tell the user it still couldn't be "
        f"placed and that nothing was charged, then offer them a choice: try one of "
        f"the other options, search for something different, or leave it.{listing}",
    )


async def cancel_pending_order(user) -> SkillResult:
    """User declined the retry. Keep the offer list — they may want another."""
    state = conversation.load(user.phone)
    if not state.pending:
        return SkillResult(SkillStatus.OK, "Nothing pending. Carry on naturally.")

    title = state.pending.title
    conversation.cancel_pending(user.phone)
    remaining = len(state.offers)
    extra = (f" The {remaining} options from before are still on the table if they "
             f"want a different one.") if remaining else ""
    return SkillResult(
        SkillStatus.OK,
        f"Dropped the pending order for '{title}'. Confirm briefly, no fuss, and "
        f"make clear nothing was charged.{extra}",
    )


def _may_spend(user, title: str) -> SkillResult | None:
    """THE money boundary. Anyone may chat and be recommended food; only an
    authorised number may spend. Every path that touches the account owner's
    provider account — building a cart included — passes through here.

    It matters more than it looks: orders go on the ACCOUNT OWNER'S linked
    provider account, to the address stored there. An unauthorised order is food
    arriving at someone else's home for them to pay.
    FAILS CLOSED — an unset AUTHORIZED_PHONES lets nobody spend.
    """
    if authz.is_authorized_user(user.phone):
        return None

    logger.warning(
        f"[skills] BLOCKED an order attempt from {user.phone} — not in "
        f"AUTHORIZED_PHONES ({title!r})"
    )
    conversation.cancel_pending(user.phone)
    return SkillResult(
        SkillStatus.ERROR,
        "NOT AUTHORISED TO ORDER: this user may chat and get recommendations, "
        "but ordering is limited to approved numbers. Tell them warmly that "
        "you can help them decide but can't place the order for them yet, and "
        "keep being useful about the food itself. Do NOT claim an order was "
        "placed, and do NOT offer to try again.",
    )


async def _execute_pending(user, entry: dict, quantity: int = None,
                           coupon: str | None = None) -> SkillResult:
    """Place the order described by `entry`, driving the state machine on the
    outcome. Shared by the first attempt and every retry so both paths behave
    identically."""
    offer = _offer_from(entry)
    provider = registry.get(offer.provider)
    quantity = quantity or 1

    blocked = _may_spend(user, offer.title)
    if blocked:
        return blocked

    if provider is None or not hasattr(provider, "place"):
        conversation.order_failed(user.phone, "no ordering provider")
        return SkillResult(
            SkillStatus.CAPABILITY_UNAVAILABLE,
            "CAPABILITY_UNAVAILABLE: ordering isn't available for this option yet. "
            "Say so plainly and offer to keep helping them decide. Do NOT claim an "
            "order was placed.",
        )

    ctx, reason = await _ordering_context(user, provider)
    if ctx is None:
        conversation.order_failed(user.phone, f"not linked: {reason}")
        return await _link_prompt(user, provider.name, f"order {offer.title}")

    logger.info(
        f"[skills] placing {offer.title!r} on {provider.name} — offer_id="
        f"{offer.id!r} quantity={quantity} price={offer.price} coupon={coupon!r}"
    )
    try:
        # Only providers that list coupons are asked to apply one. The rest keep
        # the plain two-argument signature rather than growing a parameter they
        # would ignore.
        if getattr(provider, "supports_coupons", False):
            placed = await provider.place(offer, max(1, int(quantity)), ctx, coupon=coupon)
        else:
            placed = await provider.place(offer, max(1, int(quantity)), ctx)
    except base.ItemUnavailable as e:
        state = conversation.order_failed(user.phone, str(e))
        if state.state == conversation.State.ORDER_FAILED:
            return _exhausted(user, state)
        return SkillResult(
            SkillStatus.UNAVAILABLE_ITEM,
            f"'{offer.title}' can't be ordered right now — it's unavailable or the "
            f"store is closed. Say so, offer the other options already shown, and "
            f"ask if they want you to try this one again anyway.",
        )
    except Exception as e:
        failure, instruction = failures.instruction_for(e)
        logger.error(
            f"[skills] order failed on {offer.provider} — classified as "
            f"{failure.value}: {e!r}", exc_info=True
        )
        state = conversation.order_failed(user.phone, repr(e))
        if state.state == conversation.State.ORDER_FAILED:
            return _exhausted(user, state)

        if failure in failures.NO_RETRY:
            # Retrying cannot change any of these — a refused payment method, a
            # missing endpoint, a permission we don't have. Offering another
            # attempt would be the same lie as calling a working provider down.
            conversation.cancel_pending(user.phone)
            return SkillResult(
                SkillStatus.CONFIGURATION if failure is Failure.CONFIGURATION
                else SkillStatus.ERROR,
                f"{instruction} Nothing was charged for '{offer.title}'.",
            )
        return SkillResult(
            SkillStatus.ERROR,
            f"ORDER FAILED for '{offer.title}'. Nothing was charged. {instruction}\n"
            f"Ask if they'd like you to try again — a plain 'yes' will retry THIS "
            f"same order. Do NOT expose technical details, do NOT claim it "
            f"succeeded, and do NOT search for anything new.",
        )

    # ------------------------------------------------------------------
    # PAST THIS LINE THE ORDER EXISTS. The provider took it and someone's money
    # is committed. Everything below is bookkeeping — saving the row, closing
    # the conversation, noting what they ate. If any of it fails, the user is
    # STILL told their order was placed, because it was.
    #
    # This is not defensive padding. It is the bug that shipped: the `orders`
    # table was still the pre-pivot ERP one, so save_order raised
    # "no such column: provider" AFTER Swiggy had accepted the order. The
    # exception escaped plan() and the user was told it failed — for an order
    # sitting in their Swiggy account. Bookkeeping must never speak for the
    # provider.
    # ------------------------------------------------------------------
    logger.info(
        f"[skills] {provider.name} ACCEPTED the order — provider_order_id="
        f"{placed.order_id!r} status={placed.status} total={placed.total} "
        f"eta={placed.eta_minutes}"
    )

    def bookkeeping(label, work):
        try:
            return work()
        except Exception:
            logger.error(
                f"[skills] post-order {label} failed for a PLACED order "
                f"({provider.name} {placed.order_id!r}). The order stands; the "
                f"user is being told it succeeded.",
                exc_info=True,
            )
            return None

    if placed.needs_payment:
        # The provider made the order but wants paying first. NOTHING is placed
        # yet — saying otherwise would promise food nobody has paid for.
        return await _await_payment(user, offer, placed, provider, ctx)

    order_id = bookkeeping("save_order", lambda: db.save_order(
        phone=user.phone,
        provider=placed.provider,
        provider_order_id=placed.order_id,
        status=placed.status,
        title=offer.title,
        venue=offer.venue,
        total=placed.total if placed.total is not None else offer.price,
        currency=placed.currency,
        eta_minutes=placed.eta_minutes,
    ))

    # Closing the conversation matters most of the three: leaving it in ORDERING
    # strands the user mid-flow. Still not worth contradicting the provider over.
    bookkeeping("conversation close", lambda: conversation.order_succeeded(user.phone))

    from ai import memory
    bookkeeping("food memory", lambda: memory.remember_food(
        user.phone, offer.title, memory.ORDERED, offer.venue))

    # Fall back to what the offer stated. The user was shown that price and ETA
    # a moment ago; repeating them beats "amount not returned" on a confirmation.
    minutes = placed.eta_minutes if placed.eta_minutes is not None else offer.eta_minutes
    amount = placed.total if placed.total is not None else offer.price

    eta = f"about {minutes} minutes" if minutes is not None else "no ETA available yet"
    total = f"{placed.currency} {amount:g}" if amount is not None else "amount not returned"
    # The provider's own order id — what they'd quote to the platform. The
    # internal row id is ours and stays out of the conversation.
    reference = f" Order ID {placed.order_id}." if placed.order_id else ""
    # Only ever stated when the provider confirmed the coupon went on. The
    # total already reflects it, so this just names what got them the price.
    saving = f" A coupon was applied automatically ({placed.note})." \
        if placed.note and "coupon" in placed.note else ""

    return SkillResult(
        SkillStatus.OK,
        f"ORDER PLACED. {offer.title}"
        + (f" from {offer.venue}" if offer.venue else "")
        + f". Total: {total}. ETA: {eta}.{reference}{saving}\n"
        f"Confirm it warmly in one or two lines: say it's placed, give the total, "
        f"the ETA if there is one, and the Order ID if there is one. Mention they "
        f"can ask where it is any time. Do NOT invent a delivery time.",
        offers=[offer],
    )


async def _await_payment(user, offer, placed, provider, ctx) -> SkillResult:
    """Hand the user the provider's payment page and watch for it to land.

    The link is the ONLY thing that goes to them — no card or UPI details pass
    through us at any point. A background watcher confirms the order and
    messages them when it resolves, because that happens on their clock, not
    inside this turn.

    THE LINK IS SENT HERE, not left for the model to include in its reply. A UPI
    session is short — Swiggy asked us to poll for sixty seconds — and observed
    live, the model spent twenty-four of those writing a sentence around the
    link. The user got it with a third of their window already gone.
    """
    from ai import payments

    order_id = None
    try:
        order_id = db.save_order(
            phone=user.phone, provider=placed.provider,
            provider_order_id=placed.order_id, status=placed.status,
            title=offer.title, venue=offer.venue,
            total=placed.total,          # the provider's figure or nothing
            currency=placed.currency, eta_minutes=placed.eta_minutes,
        )
        conversation.awaiting_payment(user.phone)
    except Exception:
        logger.error("[skills] could not record a pending payment", exc_info=True)

    # Only ever the provider's own figure. The offer price is what the search
    # listed for the dish, not what this cart charges — quoting it beside a
    # payment link would be inventing the amount someone is about to pay.
    total = f"{placed.currency} {placed.total:g}" if placed.total is not None else None

    # Out the door first, before the watcher and long before the model writes
    # anything. Every second here comes off the window they have to pay in.
    await payments.notify(
        user.phone,
        f"Tap to pay for {offer.title}"
        + (f" from {offer.venue}" if offer.venue else "")
        + (f" — {total}" if total else "")
        + f", it opens your UPI app:\n{placed.payment_url}",
    )
    payments.watch(user.phone, provider, placed, ctx, offer.title, order_id)
    logger.info(f"[skills] {offer.title!r} awaiting payment — order {placed.order_id}")

    return SkillResult(
        SkillStatus.OK,
        f"PAYMENT NEEDED before this order is placed. {offer.title}"
        + (f" from {offer.venue}" if offer.venue else "")
        + (f", {total}" if total else ", amount shown on the payment page") + ".\n"
        f"The payment link has ALREADY been sent to them in a separate message. "
        f"Do NOT repeat it, do NOT write out any URL. Add one short line only: "
        f"they pay whenever they're ready and you'll confirm here the moment it "
        f"lands, so there's nothing to reply to. Do NOT say the order is placed, "
        f"do NOT invent an ETA, and never ask for a UPI id, PIN or card details.",
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
