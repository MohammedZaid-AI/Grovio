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


def rank(offers: list, user: UserModel, limit: int = 5) -> list:
    """Score offers highest-first, dropping anything the user must avoid."""
    avoid = user.avoids()
    favourites = [f.lower() for f in user.favourites()]
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

        if budget is not None and offer.price is not None:
            if offer.price <= budget:
                score += 2.0
                reasons.append(f"within your {offer.currency} {budget:g} budget")
            else:
                score -= 2.0
                reasons.append(f"over your {offer.currency} {budget:g} budget")

        if offer.rating is not None:
            score += offer.rating
            reasons.append(f"rated {offer.rating}")

        if offer.eta_minutes is not None:
            score += max(0.0, 2.0 - offer.eta_minutes / 30.0)
            reasons.append(f"arrives in about {offer.eta_minutes} min")

        if offer.distance_km is not None:
            score += max(0.0, 1.5 - offer.distance_km / 5.0)
            reasons.append(f"{offer.distance_km:g} km away")

        if not reasons:
            reasons.append("matches what you asked for")

        ranked.append(Recommendation(offer=offer, score=score, reasons=tuple(reasons)))

    ranked.sort(key=lambda r: r.score, reverse=True)
    return ranked[:limit]
