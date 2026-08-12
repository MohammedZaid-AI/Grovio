"""
Messaging layer.

Import from here, never from a transport module directly:

    from whatsapp import send_text, mark_read

Two transports, selected by WHATSAPP_PROVIDER:

    cloud   WhatsApp Business Cloud API (Meta official). PRODUCTION. Default.
    local   personal WhatsApp Web session via neonize. DEVELOPMENT ONLY —
            unofficial, the number can be banned, and there is no webhook
            signature to verify. See whatsapp/local_client.py.

Nothing above this package can tell which is running: both expose the same
`send_text`, `mark_read` and `classify_send_error`, and both hand inbound
messages to `backend.whatsapp_worker.enqueue_and_wake` in the same shape.

Webhook helpers (`verify_signature`, `parse_inbound`, `parse_statuses`,
`verify_token_matches`) always come from the Cloud API module. They are Meta
protocol parsers — in `local` mode the webhook simply never fires, and leaving
them bound means backend/routes.py needs no knowledge of the choice.
"""
import os

# Always available: the webhook surface, and the one phone format the whole
# system stores. canonical_phone is shared by both transports so a user has the
# same key whichever one wrote the row.
from whatsapp.cloud_api import (  # noqa: F401
    SendErrorClass,
    canonical_phone,
    parse_errors,
    parse_inbound,
    parse_statuses,
    verify_signature,
    verify_token_matches,
)

PROVIDER = os.getenv("WHATSAPP_PROVIDER", "cloud").strip().lower()

if PROVIDER == "local":
    from whatsapp import local_client as _transport
elif PROVIDER == "cloud":
    from whatsapp import cloud_api as _transport
else:
    raise RuntimeError(
        f"WHATSAPP_PROVIDER={PROVIDER!r} is not a transport. Use 'cloud' "
        f"(production) or 'local' (development)."
    )

NotConfigured = _transport.NotConfigured
api_version = _transport.api_version
classify_send_error = _transport.classify_send_error
is_configured = _transport.is_configured
mark_read = _transport.mark_read
send_document = _transport.send_document
send_image = _transport.send_image
send_template = _transport.send_template
send_text = _transport.send_text


async def start() -> None:
    """Bring the transport up, if it needs bringing up.

    The Cloud API is stateless — Meta pushes to our webhook, so there is
    nothing to start. The local transport has to open and hold a socket.
    """
    starter = getattr(_transport, "start", None)
    if starter is not None:
        await starter()


async def stop() -> None:
    stopper = getattr(_transport, "stop", None)
    if stopper is not None:
        await stopper()


__all__ = [
    "PROVIDER", "NotConfigured", "SendErrorClass", "api_version",
    "canonical_phone", "classify_send_error", "is_configured", "mark_read",
    "parse_errors", "parse_inbound", "parse_statuses", "send_document",
    "send_image", "send_template", "send_text", "start", "stop",
    "verify_signature", "verify_token_matches",
]
