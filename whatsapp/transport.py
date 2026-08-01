"""
WhatsApp transport seam.

The delivery worker imports `send_whatsapp` and `classify_send_error` from here
and never from a vendor module, so switching providers touches this file only.

Selected by WHATSAPP_TRANSPORT:
    cloud   — WhatsApp Cloud API (Meta official). Default.
    twilio  — legacy, retained until the Cloud API number is live.

The interface every transport presents to the worker:
    async send_whatsapp(to, body) -> provider message id, raising on failure
    classify_send_error(exc)      -> SendErrorClass(retryable, code, reason)

`send_whatsapp` is always awaitable here even when the underlying SDK is
blocking, so the worker never has to know which vendor is in play.
"""
import asyncio
import os

TRANSPORT = os.getenv("WHATSAPP_TRANSPORT", "cloud").strip().lower()

if TRANSPORT == "twilio":
    from whatsapp import twilio as _impl

    classify_send_error = _impl.classify_send_error

    async def send_whatsapp(to, body):
        # Twilio's SDK is blocking — keep it off the event loop so other
        # phones' workers keep making progress.
        return await asyncio.to_thread(_impl.send_whatsapp, to, body)

else:
    from whatsapp.cloud_api import classify_send_error, send_whatsapp  # noqa: F401

__all__ = ["send_whatsapp", "classify_send_error", "TRANSPORT"]
