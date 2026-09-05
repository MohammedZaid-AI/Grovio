"""
Messaging layer.

Import from here, never from the transport module directly:

    from whatsapp import send_text, canonical_phone

ONE transport: the Baileys WhatsApp gateway, reached over plain HTTP.

    WhatsApp  <->  Baileys gateway (Node)  <->  this backend

The backend never imports Baileys and never sees a Baileys object. It posts
`{phone, text}` to the gateway's `/send`, and the gateway posts inbound messages
to `/webhook/inbound` here. Anything else that spoke those two routes would drop
in without a line changing above this package.

⚠️ Baileys is an UNOFFICIAL WhatsApp Web protocol client, not the Meta WhatsApp
Business Cloud API. It is used deliberately for this prototype: WhatsApp can ban
the number, so use a spare one, never one tied to a business account. There is
no cryptographic signature on inbound messages — the shared
`WHATSAPP_GATEWAY_SECRET` is what makes the gateway trusted, which is why it
fails closed in both directions.
"""
from whatsapp.gateway import (  # noqa: F401
    MAX_MESSAGE_LENGTH,
    NotConfigured,
    SendErrorClass,
    canonical_phone,
    classify_send_error,
    gateway_url,
    is_configured,
    mark_read,
    send_audio,
    send_document,
    send_image,
    send_template,
    send_text,
    start,
    stop,
)

PROVIDER = "baileys"

__all__ = [
    "MAX_MESSAGE_LENGTH", "PROVIDER", "NotConfigured", "SendErrorClass",
    "canonical_phone", "classify_send_error", "gateway_url", "is_configured",
    "send_audio",
    "mark_read", "send_document", "send_image", "send_template", "send_text",
    "start", "stop",
]
