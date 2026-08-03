"""
Conversation state — what we are in the MIDDLE of.

Deliberately separate from ai/memory.py, which holds who someone *is*
(preferences, allergies, budget, favourites, history). Those are long-lived and
describe a person. This is short-lived and describes a transaction:

    last offer list · selected offer · pending order · retry count · last error

WHY THIS EXISTS
---------------
Before this module, "are we in the middle of something?" was answered by the LLM
re-reading chat prose. Tool calls and their results lived in a list local to one
`plan()` call and were thrown away at the end of the turn, so the next turn saw
only English. Asked to retry a failed order, the model would sometimes start a
fresh search instead — an infinite recommendation loop.

State that drives behaviour cannot live in a model's interpretation of text. It
lives here, it is persisted, and transitions are explicit.

Every transition is validated: an illegal one raises rather than silently
corrupting the flow.
"""
import json
import os
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone

import db
from core.logger import logger

# How long a shown list stays selectable, and a pending order retryable. Long
# enough to think about, short enough that "yes" can't mean yesterday.
TTL_MINUTES = int(os.getenv("CONVERSATION_TTL_MINUTES", "45"))

# Attempts AFTER the first failure. 2 means: try, fail, retry, fail, retry,
# fail -> give up and offer alternatives.
MAX_RETRIES = int(os.getenv("ORDER_MAX_RETRIES", "2"))


class State:
    IDLE = "IDLE"
    RECOMMENDING = "RECOMMENDING"
    AWAITING_SELECTION = "AWAITING_SELECTION"
    ORDERING = "ORDERING"
    AWAITING_RETRY_CONFIRMATION = "AWAITING_RETRY_CONFIRMATION"
    ORDER_COMPLETE = "ORDER_COMPLETE"
    ORDER_FAILED = "ORDER_FAILED"


# Which transitions are legal. Anything not listed is a bug, not a possibility.
ALLOWED = {
    State.IDLE:                        {State.RECOMMENDING, State.AWAITING_SELECTION, State.IDLE},
    State.RECOMMENDING:                {State.AWAITING_SELECTION, State.IDLE},
    State.AWAITING_SELECTION:          {State.ORDERING, State.AWAITING_SELECTION, State.IDLE},
    State.ORDERING:                    {State.ORDER_COMPLETE, State.AWAITING_RETRY_CONFIRMATION,
                                        State.ORDER_FAILED, State.IDLE},
    State.AWAITING_RETRY_CONFIRMATION: {State.ORDERING, State.ORDER_FAILED,
                                        State.AWAITING_SELECTION, State.IDLE},
    # Terminal states accept themselves: asking again after giving up is a
    # normal thing for a person to do, and must be idempotent rather than an
    # error.
    State.ORDER_COMPLETE:              {State.IDLE, State.AWAITING_SELECTION,
                                        State.RECOMMENDING, State.ORDER_COMPLETE},
    State.ORDER_FAILED:                {State.IDLE, State.AWAITING_SELECTION,
                                        State.RECOMMENDING, State.ORDER_FAILED},
}


class IllegalTransition(RuntimeError):
    pass


@dataclass
class PendingOrder:
    """Everything needed to retry an order WITHOUT asking the user again."""
    provider: str
    offer_id: str
    title: str
    venue: str | None = None
    price: float | None = None
    currency: str = "INR"
    eta_minutes: int | None = None
    quantity: int = 1
    selection: int | None = None      # the number the user actually said
    retry_count: int = 0
    last_error: str | None = None     # for logs; never shown to the user
    created_at: str = ""

    @property
    def retries_exhausted(self) -> bool:
        return self.retry_count >= MAX_RETRIES

    def describe(self) -> str:
        bits = [self.title]
        if self.venue:
            bits.append(f"from {self.venue}")
        if self.price is not None:
            bits.append(f"{self.currency} {self.price:g}")
        return " · ".join(bits)


@dataclass
class Conversation:
    phone: str
    state: str = State.IDLE
    offers: list = field(default_factory=list)
    query: str | None = None
    pending: PendingOrder | None = None
    stale: bool = False

    @property
    def has_offers(self) -> bool:
        return bool(self.offers) and not self.stale

    @property
    def awaiting_retry(self) -> bool:
        return self.state == State.AWAITING_RETRY_CONFIRMATION and self.pending is not None

    def offer_at(self, selection: int):
        """1-based, as the user sees it. None if out of range."""
        index = int(selection) - 1
        if 0 <= index < len(self.offers):
            return self.offers[index]
        return None


# ----------------------------------------------------------------------
# Persistence
# ----------------------------------------------------------------------
def _expired(updated_at) -> bool:
    if not updated_at:
        return True
    try:
        stamp = datetime.strptime(str(updated_at)[:19], "%Y-%m-%d %H:%M:%S")
    except ValueError:
        return True
    return stamp + timedelta(minutes=TTL_MINUTES) < datetime.now(timezone.utc).replace(tzinfo=None)


def load(phone: str) -> Conversation:
    """Current state. An expired conversation reads as IDLE — a stale 'yes'
    must never resurrect an order from an hour ago."""
    row = db.get_conversation_state(phone)
    if not row:
        return Conversation(phone=phone)

    if _expired(row["updated_at"]):
        return Conversation(phone=phone, stale=True)

    try:
        offers = json.loads(row["offers"]) if row["offers"] else []
    except (TypeError, ValueError):
        offers = []

    pending = None
    if row["pending"]:
        try:
            pending = PendingOrder(**json.loads(row["pending"]))
        except (TypeError, ValueError):
            pending = None

    return Conversation(
        phone=phone,
        state=row["state"] or State.IDLE,
        offers=offers,
        query=row["query"],
        pending=pending,
    )


def _save(conversation: Conversation, new_state: str) -> Conversation:
    current = conversation.state if not conversation.stale else State.IDLE
    if new_state not in ALLOWED.get(current, set()):
        raise IllegalTransition(f"{current} -> {new_state}")

    db.save_conversation_state(
        phone=conversation.phone,
        state=new_state,
        offers=json.dumps(conversation.offers) if conversation.offers else None,
        query=conversation.query,
        pending=json.dumps(asdict(conversation.pending)) if conversation.pending else None,
    )
    conversation.state = new_state
    conversation.stale = False
    logger.info(f"[conversation] {current} -> {new_state}")
    return conversation


# ----------------------------------------------------------------------
# Transitions — the only ways state changes
# ----------------------------------------------------------------------
def show_offers(phone: str, offers: list, query: str) -> Conversation:
    """Options were presented; we now await a choice."""
    conversation = load(phone)
    conversation.offers = offers
    conversation.query = query
    conversation.pending = None
    if conversation.stale:
        conversation.state = State.IDLE
    return _save(conversation, State.AWAITING_SELECTION)


def begin_order(phone: str, pending: PendingOrder) -> Conversation:
    """A specific offer was chosen and is being placed."""
    conversation = load(phone)
    pending.created_at = pending.created_at or datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    conversation.pending = pending
    if conversation.stale:
        conversation.state = State.IDLE
        conversation.state = State.AWAITING_SELECTION   # begin_order follows a selection
    return _save(conversation, State.ORDERING)


def order_failed(phone: str, error: str) -> Conversation:
    """The provider refused. Keep the pending order intact so a plain 'yes'
    retries THIS order rather than starting a new search."""
    conversation = load(phone)
    if conversation.pending:
        conversation.pending.last_error = (error or "")[:300]

    if conversation.pending and conversation.pending.retries_exhausted:
        return _save(conversation, State.ORDER_FAILED)
    return _save(conversation, State.AWAITING_RETRY_CONFIRMATION)


def begin_retry(phone: str) -> Conversation:
    """Retry the SAME pending order. Increments the counter so this terminates."""
    conversation = load(phone)
    if not conversation.pending:
        raise IllegalTransition("retry with no pending order")
    conversation.pending.retry_count += 1
    return _save(conversation, State.ORDERING)


def order_succeeded(phone: str) -> Conversation:
    conversation = load(phone)
    conversation.pending = None
    conversation.offers = []      # ordered, so the list is spent
    return _save(conversation, State.ORDER_COMPLETE)


def give_up(phone: str) -> Conversation:
    """Retries exhausted. Offers survive so alternatives can be suggested."""
    conversation = load(phone)
    return _save(conversation, State.ORDER_FAILED)


def cancel_pending(phone: str) -> Conversation:
    """User declined the retry. Drop the order, keep the offers — they may well
    want a different one from the same list."""
    conversation = load(phone)
    conversation.pending = None
    target = State.AWAITING_SELECTION if conversation.offers else State.IDLE
    return _save(conversation, target)


def reset(phone: str) -> None:
    db.clear_conversation_state(phone)


# ----------------------------------------------------------------------
# Intent — resolved in code, not by the model
# ----------------------------------------------------------------------
# When the state machine is waiting on a determinate answer ("shall I retry?"),
# the reply is classified here. Asking the LLM to decide would reintroduce
# exactly the non-determinism this module exists to remove.

AFFIRMATIVE = "AFFIRMATIVE"
NEGATIVE = "NEGATIVE"
ALTERNATIVE = "ALTERNATIVE"
UNCLEAR = "UNCLEAR"

_YES = {
    "y", "ya", "yes", "yeah", "yep", "yup", "yess", "sure", "ok", "okay", "okey",
    "k", "kk", "fine", "please", "pls", "confirm", "confirmed", "go", "proceed",
    "continue", "retry", "again", "haan", "haa", "han", "ha",
}
_YES_PHRASES = (
    "go ahead", "please do", "do it", "send it", "try again", "give it another",
    "one more time", "yes please", "go for it", "place it", "order it", "theek hai",
)
_NO = {"n", "no", "nope", "nah", "naa", "cancel", "stop", "dont", "don't", "nevermind"}
_NO_PHRASES = ("never mind", "forget it", "leave it", "not now", "no thanks", "drop it")
_ALT_PHRASES = (
    "something else", "anything else", "other option", "other options", "another one",
    "another option", "different", "show me other", "show other", "alternatives",
    "alternative", "someone else", "somewhere else", "other restaurant", "change it",
)


def _normalise(text: str) -> str:
    cleaned = "".join(c if c.isalnum() or c.isspace() else " " for c in (text or "").lower())
    return " ".join(cleaned.split())


def classify_reply(text: str) -> str:
    """Classify a reply to a yes/no question.

    Order matters: "no, show me others" is a request for alternatives, which is
    more actionable than reading it as a plain refusal.
    """
    normalised = _normalise(text)
    if not normalised:
        return UNCLEAR

    if any(phrase in normalised for phrase in _ALT_PHRASES):
        return ALTERNATIVE
    if any(phrase in normalised for phrase in _NO_PHRASES):
        return NEGATIVE
    if any(phrase in normalised for phrase in _YES_PHRASES):
        return AFFIRMATIVE

    words = normalised.split()
    if words[0] in _NO or (len(words) <= 3 and any(w in _NO for w in words)):
        return NEGATIVE
    if words[0] in _YES or (len(words) <= 3 and any(w in _YES for w in words)):
        return AFFIRMATIVE
    return UNCLEAR


_ORDINALS = {
    "first": 1, "1st": 1, "one": 1, "top": 1, "initial": 1,
    "second": 2, "2nd": 2, "two": 2, "middle": 2,
    "third": 3, "3rd": 3, "three": 3,
    "fourth": 4, "4th": 4, "four": 4,
    "fifth": 5, "5th": 5, "five": 5,
    "last": -1, "final": -1, "bottom": -1,
}


def resolve_selection(text: str, count: int):
    """Turn a reply into a 1-based option number, or None.

    Handles "1", "one", "first", "first one", "option 1", "number one",
    "go with the first", "let's do the first one", "the top one", "the last one".
    Returns None when genuinely ambiguous rather than guessing — an ambiguous
    guess here spends someone's money on the wrong thing.
    """
    if count <= 0:
        return None
    normalised = _normalise(text)
    if not normalised:
        return None
    words = normalised.split()

    # "option 2" / "number 3" / "#1"
    for marker in ("option", "number", "no", "item", "choice"):
        if marker in words:
            position = words.index(marker)
            if position + 1 < len(words) and words[position + 1].isdigit():
                value = int(words[position + 1])
                return value if 1 <= value <= count else None

    for word in words:
        if word in _ORDINALS:
            value = _ORDINALS[word]
            value = count if value == -1 else value
            return value if 1 <= value <= count else None

    digits = [w for w in words if w.isdigit()]
    if len(digits) == 1:
        # A bare number in a short reply is a choice; inside a sentence it is
        # more likely a quantity ("2 portions of the biryani").
        if len(words) <= 4:
            value = int(digits[0])
            return value if 1 <= value <= count else None

    if count == 1 and ("that one" in normalised or "this one" in normalised):
        return 1
    return None
