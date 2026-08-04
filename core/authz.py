"""
Centralized phone-number authorization for WhatsApp-initiated actions.

SECURITY: Every allowlist here FAILS CLOSED. If the backing environment
variable is not configured, access is denied for everyone. A missing
config must never grant access.

This is a leaf module (imports only `os`) so it can be imported from anywhere
without circular imports.
"""
import os


def _phone_in_allowlist(from_phone: str, env_var: str) -> bool:
    """Return True only if `from_phone` is present in the comma-separated
    allowlist stored in the `env_var` environment variable.

    Fails CLOSED: an unset/blank env var denies everyone.
    """
    config = os.getenv(env_var, "").strip()

    # NOT configured -> block all (fail closed)
    if not config:
        return False

    allow_list = [p.strip() for p in config.split(",") if p.strip()]

    # Compare on digits only, so an allowlist entry written "+91 97..." matches
    # the bare MSISDN the Cloud API delivers.
    def digits(value):
        return "".join(c for c in (value or "") if c.isdigit())

    return digits(from_phone) in {digits(entry) for entry in allow_list}


def is_authorized_user(from_phone: str) -> bool:
    """Authorized to spend money — i.e. to place an order.

    Fails CLOSED if AUTHORIZED_PHONES is not configured.
    """
    return _phone_in_allowlist(from_phone, "AUTHORIZED_PHONES")
