"""
Messaging layer.

Import from here, never from `whatsapp.cloud_api` directly:

    from whatsapp import send_text, mark_read

One provider — the WhatsApp Business Cloud API. This package is the seam: if a
second transport ever exists, it is introduced here and nothing above changes.
Twilio was removed in the Cloud API migration; see MIGRATION_CLOUD_API.md.
"""
from whatsapp.cloud_api import (  # noqa: F401
    NotConfigured,
    SendErrorClass,
    api_version,
    canonical_phone,
    classify_send_error,
    is_configured,
    mark_read,
    parse_errors,
    parse_inbound,
    parse_statuses,
    send_document,
    send_image,
    send_template,
    send_text,
    verify_signature,
    verify_token_matches,
)

__all__ = [
    "NotConfigured", "SendErrorClass", "api_version", "canonical_phone",
    "classify_send_error", "is_configured", "mark_read", "parse_errors",
    "parse_inbound", "parse_statuses", "send_document", "send_image",
    "send_template", "send_text", "verify_signature", "verify_token_matches",
]
