"""
Recommendation engine.

Ranks real offers against the user model and returns, for each one, the reasons
that actually drove its score.

WHY SCORING IS DETERMINISTIC: the "why" must be true. If the LLM both picked and
justified the choice it could rationalise anything. Instead this layer decides
using real fields, emits factual reasons, and the LLM only phrases them. An
explanation can therefore never reference a rating, price or ETA that no
provider returned.

Signals used when present: budget fit, favourites, rating, ETA, distance,
availability. Missing data is skipped, never guessed. Anything the user must
avoid is filtered out entirely — allergies are not a ranking penalty.

Phase 5 deepens this (time of day, weekday/weekend habits, cuisine affinity).
"""
import re
from dataclasses import dataclass

from ai.memory import UserModel
from ai.providers.base import Offer


@dataclass(frozen=True)
class Recommendation:
    offer: Offer
    score: float
    reasons: tuple

    def explain(self) -> str:
        return f"{self.offer.summary()} — {'; '.join(self.reasons)}"


def _budget_of(user: UserModel) -> float | None:
    raw = str(user.facts.get("budget", "")).replace("₹", "").replace(",", "").strip()
    try:
        return float(raw) if raw else None
    except ValueError:
        return None


def _canon(text: str) -> str:
    """Lowercase, keep words only, crudely singularise, and pad — so matching is
    word-for-word. Padding matters: a naive substring test would let an 'ice'
    allergy filter out 'rice'."""
    words = (word.rstrip("s") for word in re.findall(r"[a-z]+", text.lower()))
    return f" {' '.join(words)} "


def _is_avoided(offer: Offer, avoid: list) -> bool:
    """Literal-name match against the avoid list.

    ponytail: this catches an item NAMED after the allergen ("peanuts" ->
    "Peanut Salad") but cannot know that lobster IS shellfish or that a korma
    contains nuts — that needs ingredient data or a semantic check per offer.
    Filtering is therefore a backstop, NOT the safety guarantee: the avoid list
    also goes into the system prompt as MUST AVOID so the model applies its own
    food knowledge before recommending. Upgrade path is ingredient/allergen tags
    from the provider, which is the only way to make this airtight.
    """
    haystack = _canon(" ".join(filter(None, [offer.title, offer.venue or ""])))
    for term in avoid:
        canonical = _canon(term).strip()
        if canonical and f" {canonical} " in haystack:
            return True
    return False


# Delivery ratings cluster in a narrow band — almost everything is 3.8–4.6 —
# so adding the raw number made rating dominate the total while barely
# separating anything. Stretching that band is what lets 4.6 actually beat 4.1.
RATING_FLOOR = 3.5
RATING_CEILING = 5.0
GREAT_RATING = 4.3

# Above this, a place is "a bit further" rather than round the corner. Used only
# to EXPLAIN a trade-off, never to rule somewhere out.
NEARBY_MINUTES = 25

# At most this many dishes from one venue in the final list. Five things from
# the same kitchen is a menu, not a choice.
MAX_PER_VENUE = 2


def _rating_score(offer) -> float:
    """0–3, stretched across the band ratings actually occupy."""
    if offer.rating is None:
        return 0.0
    span = RATING_CEILING - RATING_FLOOR
    return 3.0 * max(0.0, min(1.0, (offer.rating - RATING_FLOOR) / span))


def _speed_score(offer) -> float:
    """0–1.5, and only ever a BONUS for being quick.

    Nothing is penalised for being further away. A friend who only ever
    suggested the closest place would be no use — the whole point is that
    somewhere twenty minutes further can be worth it, and that is a trade-off
    to explain rather than a reason to hide the option.
    """
    if offer.eta_minutes is None:
        return 0.0
    return max(0.0, 1.5 * (1.0 - offer.eta_minutes / 60.0))


def _tried(user: UserModel) -> set:
    """Everything they have ordered or liked before, lowercased."""
    return {entry["item"].lower() for entry in user.food}


def _diversify(ranked: list, limit: int) -> list:
    """Take the best, but not five things from one kitchen.

    Order within the list is unchanged — this only skips a venue's third dish
    in favour of the next-best somewhere else, then backfills if that left the
    list short.
    """
    picked, per_venue = [], {}
    for rec in ranked:
        venue = (rec.offer.venue or rec.offer.title).lower()
        if per_venue.get(venue, 0) >= MAX_PER_VENUE:
            continue
        per_venue[venue] = per_venue.get(venue, 0) + 1
        picked.append(rec)
        if len(picked) == limit:
            return picked

    # Not enough variety to fill the list — better a shorter honest list padded
    # with the next best than a list that pretends more choice exists.
    for rec in ranked:
        if rec not in picked:
            picked.append(rec)
            if len(picked) == limit:
                break
    return picked


def rank(offers: list, user: UserModel, limit: int = 6) -> list:
    """Score offers highest-first, dropping anything the user must avoid."""
    avoid = user.avoids()
    favourites = [f.lower() for f in user.favourites()]
    tried = _tried(user)
    budget = _budget_of(user)

    ranked = []
    for offer in offers:
        if not offer.available or _is_avoided(offer, avoid):
            continue

        score = 0.0
        reasons = []
        title = offer.title.lower()

        if any(fav in title or title in fav for fav in favourites):
            score += 3.0
            reasons.append("you order this regularly")
        elif tried and title not in tried:
            # A friend does not read back your order history. Small, so it
            # breaks ties rather than steering the whole list.
            score += 0.75
            reasons.append("something you haven't tried")

        if budget is not None and offer.price is not None:
            if offer.price <= budget:
                score += 2.0
                reasons.append(f"within your {offer.currency} {budget:g} budget")
            else:
                score -= 2.0
                reasons.append(f"over your {offer.currency} {budget:g} budget")

        rating_score = _rating_score(offer)
        score += rating_score
        if offer.rating is not None:
            reasons.append(f"rated {offer.rating}")

        score += _speed_score(offer)
        if offer.eta_minutes is not None:
            if offer.eta_minutes > NEARBY_MINUTES and offer.rating is not None \
                    and offer.rating >= GREAT_RATING:
                # The one case worth spelling out: further, but good enough that
                # a friend would still say go for it.
                reasons.append(
                    f"{offer.eta_minutes} min away rather than round the corner, "
                    f"but rated {offer.rating}"
                )
            else:
                reasons.append(f"arrives in about {offer.eta_minutes} min")

        if offer.distance_km is not None:
            score += max(0.0, 1.5 - offer.distance_km / 5.0)
            reasons.append(f"{offer.distance_km:g} km away")

        if not reasons:
            reasons.append("matches what you asked for")

        ranked.append(Recommendation(offer=offer, score=score, reasons=tuple(reasons)))

    # Round before sorting. Two options that score 2.65 and 2.6500000000000004
    # are the same option as far as anyone eating dinner is concerned, and
    # letting floating-point noise decide which one is listed first makes the
    # order unreproducible for no reason. A genuine tie keeps the provider's own
    # order, which is its relevance ranking — a better tiebreak than luck.
    ranked.sort(key=lambda r: -round(r.score, 3))
    return _diversify(ranked, limit)
