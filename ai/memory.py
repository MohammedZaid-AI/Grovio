"""
Memory — the user model.

Identity is the WhatsApp phone number. There is no signup and no profile
screen: everything here is learned from conversation and persists forever.

Three layers:
  * facts    — long-term preferences (allergies, budget, home, cuisines, ...)
  * history  — durable conversation turns
  * food     — what they ordered, loved, or turned down

This module knows nothing about providers, restaurants, or Swiggy. It is a
store of what the *person* is like.
"""
from dataclasses import dataclass, field

import db

# Preferences that must never be ignored when recommending. Kept as a hint for
# prompt-building and ranking — not a closed list; any key can be stored.
SAFETY_CRITICAL = ("allergies", "dislikes", "dietary")

# Sentiments recorded in food memory.
ORDERED, LIKED, DISLIKED, REJECTED = "ORDERED", "LIKED", "DISLIKED", "REJECTED"
VALID_SENTIMENTS = (ORDERED, LIKED, DISLIKED, REJECTED)

HISTORY_TURNS = 20


@dataclass
class UserModel:
    """Everything known about one person, assembled for a single turn."""
    phone: str
    facts: dict = field(default_factory=dict)
    history: list = field(default_factory=list)
    food: list = field(default_factory=list)

    @property
    def is_new(self) -> bool:
        return not any(not k.startswith("_") for k in self.facts) and not self.history

    def favourites(self, limit=5) -> list:
        """Most frequently ordered or liked items, most common first."""
        counts = {}
        for entry in self.food:
            if entry["sentiment"] in (ORDERED, LIKED):
                counts[entry["item"]] = counts.get(entry["item"], 0) + 1
        return sorted(counts, key=counts.get, reverse=True)[:limit]

    def avoids(self) -> list:
        """Items to steer away from — explicit dislikes plus stated aversions."""
        avoid = {e["item"].lower() for e in self.food if e["sentiment"] == DISLIKED}
        for key in ("dislikes", "allergies"):
            raw = self.facts.get(key, "")
            avoid.update(part.strip().lower() for part in raw.split(",") if part.strip())
        return sorted(avoid)

    def describe(self) -> str:
        """A compact profile for the system prompt. Only real, stored data —
        never inferred, so the model cannot 'remember' something invented."""
        if self.is_new:
            return "This is a new user. Nothing is known about them yet."

        lines = []
        # Keys starting with "_" are our own bookkeeping (what has been imported,
        # when) and are not things anyone told us about themselves. They stay out
        # of the prompt so the model never reads them back as a preference.
        shown = {k: v for k, v in self.facts.items() if not k.startswith("_")}
        if shown:
            lines.append("Known preferences:")
            lines += [f"  - {key}: {value}" for key, value in sorted(shown.items())]
        favourites = self.favourites()
        if favourites:
            lines.append(f"Frequently orders: {', '.join(favourites)}")
        avoids = self.avoids()
        if avoids:
            lines.append(f"MUST AVOID: {', '.join(avoids)}")
        return "\n".join(lines)


def load(phone: str) -> UserModel:
    """Assemble the user model. This is the planner's memory-retrieval step."""
    return UserModel(
        phone=phone,
        facts=db.get_user_facts(phone),
        history=db.get_history(phone, limit=HISTORY_TURNS),
        food=db.get_food_memory(phone),
    )


def remember_fact(phone: str, key: str, value: str) -> str:
    db.set_user_fact(phone, key, value)
    return f"Saved {key} = {value}" if value else f"Forgot {key}"


def remember_food(phone: str, item: str, sentiment: str, venue: str | None = None) -> str:
    sentiment = (sentiment or "").upper()
    if sentiment not in VALID_SENTIMENTS:
        raise ValueError(f"sentiment must be one of {VALID_SENTIMENTS}, got {sentiment!r}")
    db.add_food_memory(phone, item, sentiment, venue)
    return f"Recorded {item} as {sentiment}"


def record_turn(phone: str, user_message: str, assistant_reply: str) -> None:
    db.add_history(phone, "user", user_message)
    db.add_history(phone, "assistant", assistant_reply)
