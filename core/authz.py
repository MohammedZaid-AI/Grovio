"""
Centralized phone-number authorization for WhatsApp-initiated actions.

SECURITY: Every allowlist here FAILS CLOSED. If the backing environment
variable is not configured, access is denied for everyone. A missing
config must never grant access.

This is a leaf module (imports only `os`) so it can be safely imported
from both backend.chat and ai.langgraph.nodes without circular imports.
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

    # Normalize incoming phone (strip Twilio "whatsapp:" prefix)
    normalized_phone = (from_phone or "").replace("whatsapp:", "").strip()

    return normalized_phone in allow_list


def is_inventory_admin(from_phone: str) -> bool:
    """Authorized to run inventory SET/ADD/REMOVE commands.

    Fails CLOSED if INVENTORY_ADMIN_PHONES is not configured.
    """
    return _phone_in_allowlist(from_phone, "INVENTORY_ADMIN_PHONES")


def is_authorized_user(from_phone: str) -> bool:
    """Authorized to run money/approval/financial business actions:
    grocery ordering, purchase-order approval/rejection, and
    financial / dashboard / COO report queries.

    Fails CLOSED if AUTHORIZED_PHONES is not configured.
    """
    return _phone_in_allowlist(from_phone, "AUTHORIZED_PHONES")
