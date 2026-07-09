"""
Outbound WhatsApp delivery via the Twilio REST API.

Phase 2 moves reply delivery OFF the webhook's TwiML response (which Twilio
discards if the webhook is slow) and onto the REST API, which has no webhook
timeout. The background worker (backend/whatsapp_worker.py) calls send_whatsapp
for every reply part.

Required environment variables:
    TWILIO_ACCOUNT_SID
    TWILIO_AUTH_TOKEN
    TWILIO_WHATSAPP_FROM   e.g. "whatsapp:+14155238886" (the Twilio sender)
"""
import os
from collections import namedtuple

from twilio.rest import Client
from twilio.base.exceptions import TwilioRestException


# ----------------------------------------------------------------------
# Send-error classification (Twilio retry handling)
# ----------------------------------------------------------------------
# Result of classifying a send failure: whether a retry can ever help, the
# Twilio error code (if any, for the outbound table), and a concise reason.
SendErrorClass = namedtuple("SendErrorClass", ["retryable", "code", "reason"])

# Twilio error codes that are PERMANENT for this message — retrying wastes
# attempts and delays the FAILED verdict. Keyed by Twilio's numeric error code.
NON_RETRYABLE_TWILIO_CODES = {
    63038,  # account exceeded the daily message limit
    20003,  # authentication failed (invalid credentials)
    20404,  # resource not found (bad account/number)
    21211,  # invalid 'To' phone number (invalid recipient)
    21212,  # invalid 'From' phone number
    21408,  # permission to send to this region is not enabled
    21606,  # 'From' is not a valid, message-capable Twilio number (invalid sender)
    21610,  # recipient has unsubscribed / is blocked
    21612,  # 'To' cannot be routed / unreachable
    21614,  # 'To' is not a valid mobile number
    63007,  # invalid 'From' — WhatsApp sender not found (invalid sender)
    63016,  # freeform message outside the allowed session window
}


def classify_send_error(exc):
    """Classify a send exception as retryable (transient) or not (permanent).

    Rules:
      * Missing configuration      -> permanent (retrying won't add env vars).
      * Known permanent Twilio code -> permanent (e.g. 63038 daily limit).
      * HTTP 401/403 (auth)         -> permanent.
      * HTTP 5xx / 429 (no permanent code) -> retryable (transient server/rate).
      * Other HTTP 4xx              -> permanent (client error: bad params/number).
      * Anything non-Twilio (network, timeout, connection reset) -> retryable.
    """
    # Our own guard in send_whatsapp — a missing sender/credentials can't self-heal.
    if isinstance(exc, RuntimeError):
        return SendErrorClass(False, None, "Twilio not configured")

    if isinstance(exc, TwilioRestException):
        code = getattr(exc, "code", None)
        status = getattr(exc, "status", None)

        if code in NON_RETRYABLE_TWILIO_CODES:
            return SendErrorClass(False, code, f"permanent Twilio error {code}")
        if status in (401, 403):
            return SendErrorClass(False, code, f"authentication failure (HTTP {status})")
        if status is not None and 500 <= status <= 599:
            return SendErrorClass(True, code, f"transient Twilio server error (HTTP {status})")
        if status == 429:
            return SendErrorClass(True, code, "transient rate limit (HTTP 429)")
        if status is not None and 400 <= status <= 499:
            suffix = f", code {code}" if code else ""
            return SendErrorClass(False, code, f"non-retryable client error (HTTP {status}{suffix})")
        # Unknown Twilio error without a clear status: retry conservatively.
        return SendErrorClass(True, code, f"Twilio error{f' {code}' if code else ''} (retrying)")

    # Network failures, timeouts, connection resets, etc. — transient.
    return SendErrorClass(True, None, f"transient network error ({type(exc).__name__})")


def _sender_number():
    return os.getenv("TWILIO_WHATSAPP_FROM") or os.getenv("TWILIO_WHATSAPP_NUMBER")


def _as_whatsapp(number):
    number = (number or "").strip()
    return number if number.startswith("whatsapp:") else f"whatsapp:{number}"


def send_whatsapp(to, body):
    """Send one WhatsApp message and return the Twilio message SID.

    Raises on any failure (missing config, network error, Twilio error) so the
    caller can retry. It NEVER swallows an error — a dropped reply must be
    visible, never silent.
    """
    account_sid = os.getenv("TWILIO_ACCOUNT_SID")
    auth_token = os.getenv("TWILIO_AUTH_TOKEN")
    from_number = _sender_number()

    if not (account_sid and auth_token and from_number):
        raise RuntimeError(
            "Twilio REST is not configured. Set TWILIO_ACCOUNT_SID, "
            "TWILIO_AUTH_TOKEN and TWILIO_WHATSAPP_FROM."
        )

    client = Client(account_sid, auth_token)
    message = client.messages.create(
        from_=_as_whatsapp(from_number),
        to=_as_whatsapp(to),
        body=body,
    )
    return message.sid
