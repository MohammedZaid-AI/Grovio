"""
Failure classification.

One question, answered in one place: *what kind of thing went wrong, and what
should the user be told?*

WHY THIS EXISTS
---------------
Every exception used to collapse into one sentence. A missing OAuth endpoint —
a configuration mistake on OUR side that no amount of waiting will fix — was
reported to the user as "the Swiggy connection is temporarily unavailable, try
again shortly". That is false twice over: Swiggy was not down, and trying again
was never going to work.

Telling someone a service is broken when it is not is the same category of
error as inventing a restaurant. It sends them away with a wrong belief.

Each failure maps to an INSTRUCTION written for the model — a statement about
reality, not a script. Nothing here is shown to a user verbatim.
"""
import json
from enum import Enum

import httpx


class Failure(str, Enum):
    CONFIGURATION = "configuration"    # ours to fix; the user can do nothing
    NOT_LINKED = "not_linked"          # the user must connect an account
    AUTHENTICATION = "authentication"  # credential rejected — re-link
    AUTHORIZATION = "authorization"    # linked, but not permitted to do this
    UNAVAILABLE = "unavailable"        # the provider really is down (5xx)
    NETWORK = "network"                # we could not reach them at all
    TIMEOUT = "timeout"                # they did not answer in time
    RATE_LIMIT = "rate_limit"          # too many requests
    PARSING = "parsing"                # they answered in a shape we can't read
    VALIDATION = "validation"          # we sent something they rejected
    CHECKOUT = "checkout"              # the order itself was refused
    ITEM_UNAVAILABLE = "item_unavailable"
    PAYMENT_UNAVAILABLE = "payment_unavailable"   # the payment method is refused
    UNKNOWN = "unknown"


# Failures no amount of retrying can clear. Offering "shall I try again?" for
# one of these is the same lie as telling a user a working provider is down.
NO_RETRY = frozenset({
    Failure.CONFIGURATION,
    Failure.PAYMENT_UNAVAILABLE,
    Failure.AUTHORIZATION,
})


# What the model is told. Written as fact + intent, never as a canned sentence,
# so the reply stays in the concierge's voice.
INSTRUCTION = {
    Failure.CONFIGURATION: (
        "CONFIGURATION ERROR on OUR side — this integration is not set up yet. "
        "The provider is NOT down and retrying will NOT help. Tell the user "
        "plainly that this connection isn't ready yet and you'll let them know "
        "when it is. Do NOT say the provider is unavailable, do NOT blame them, "
        "do NOT suggest trying again in a few minutes, and do NOT invent options."
    ),
    Failure.NOT_LINKED: (
        "NOT CONNECTED: the user hasn't linked this account yet. Say you're not "
        "connected to it yet and ask them to connect. Do NOT say it is down."
    ),
    Failure.AUTHENTICATION: (
        "CREDENTIAL EXPIRED: their connection has lapsed and needs redoing. Say "
        "so warmly in one line and offer a fresh link. This is normal and not "
        "their fault. Do NOT say the provider is down."
    ),
    Failure.AUTHORIZATION: (
        "NOT PERMITTED: the account is connected but not allowed to do this. Say "
        "you don't have permission for that, and what you CAN do instead."
    ),
    Failure.UNAVAILABLE: (
        "PROVIDER GENUINELY DOWN: they returned a server error. Say they're "
        "having trouble right now and suggest trying again shortly — this is the "
        "ONE case where that is honest."
    ),
    Failure.NETWORK: (
        "COULD NOT REACH the provider. Say you couldn't get through just now and "
        "offer to try again in a moment."
    ),
    Failure.TIMEOUT: (
        "TIMED OUT waiting for the provider. Say it's taking unusually long and "
        "offer to retry. Do NOT claim anything succeeded."
    ),
    Failure.RATE_LIMIT: (
        "RATE LIMITED: too many requests too quickly. Ask them to give it a "
        "minute. Do NOT say the provider is down."
    ),
    Failure.PARSING: (
        "UNREADABLE RESPONSE: the provider answered in a shape we can't parse. "
        "This is our bug, not an outage. Apologise briefly, say you couldn't "
        "read that back, and do NOT invent what it might have said."
    ),
    Failure.VALIDATION: (
        "REJECTED REQUEST: the provider refused what we sent as invalid. Our bug. "
        "Say you couldn't complete that, without technical detail."
    ),
    Failure.CHECKOUT: (
        "ORDER REFUSED at checkout. Nothing was charged. Say so plainly and offer "
        "the alternatives already shown."
    ),
    Failure.ITEM_UNAVAILABLE: (
        "ITEM UNAVAILABLE right now — sold out, or the store is closed. Say so "
        "and offer the other options."
    ),
    Failure.PAYMENT_UNAVAILABLE: (
        "PAYMENT METHOD REFUSED by the platform — the way we pay isn't accepted "
        "right now, and this has nothing to do with the food or the user. "
        "Nothing was charged and the order was NOT placed. Say plainly that you "
        "can't complete the payment at the moment, that their choice was fine, "
        "and that they can order it in the provider's own app meanwhile. Do NOT "
        "offer to try again — retrying cannot change it."
    ),
    Failure.UNKNOWN: (
        "UNEXPECTED FAILURE. Apologise in one line, offer to try again, and do "
        "NOT guess at a cause or blame the provider."
    ),
}

# HTTP status -> failure. 4xx is about the request, 5xx is about the server, and
# conflating the two is how "we sent a bad request" became "they are down".
_BY_STATUS = {
    400: Failure.VALIDATION,
    401: Failure.AUTHENTICATION,
    403: Failure.AUTHORIZATION,
    404: Failure.CONFIGURATION,   # an endpoint we were told exists, doesn't
    422: Failure.VALIDATION,
    429: Failure.RATE_LIMIT,
}


def from_status(status: int) -> Failure:
    if status in _BY_STATUS:
        return _BY_STATUS[status]
    if 500 <= status < 600:
        return Failure.UNAVAILABLE
    if 400 <= status < 500:
        return Failure.VALIDATION
    return Failure.UNKNOWN


def classify(error: BaseException) -> Failure:
    """Map an exception to what actually went wrong.

    Deliberately importless where it can be: provider exceptions are matched by
    class name so this module never imports the provider layer it serves.
    """
    # Anything that already knows what it is, wins.
    declared = getattr(error, "failure", None)
    if isinstance(declared, Failure):
        return declared

    if isinstance(error, httpx.TimeoutException):
        return Failure.TIMEOUT
    if isinstance(error, httpx.HTTPStatusError):
        return from_status(error.response.status_code)
    if isinstance(error, (httpx.ConnectError, httpx.NetworkError, ConnectionError)):
        return Failure.NETWORK
    if isinstance(error, (json.JSONDecodeError, UnicodeDecodeError)):
        return Failure.PARSING
    if isinstance(error, TimeoutError):
        return Failure.TIMEOUT

    name = type(error).__name__
    if name == "ItemUnavailable":
        return Failure.ITEM_UNAVAILABLE
    if name in ("ToolSurfaceMismatch", "CryptoNotConfigured"):
        return Failure.CONFIGURATION
    if name == "NoDeliveryAddress":
        return Failure.VALIDATION
    if name == "NeedsLink":
        return Failure.NOT_LINKED
    if name == "ProviderError":
        return Failure.CHECKOUT if "checkout" in str(error).lower() else Failure.UNKNOWN
    if isinstance(error, (TypeError, ValueError, KeyError)):
        return Failure.PARSING

    return Failure.UNKNOWN


def instruction_for(error: BaseException) -> tuple:
    """(failure, instruction) for one exception."""
    failure = classify(error)
    return failure, INSTRUCTION[failure]
