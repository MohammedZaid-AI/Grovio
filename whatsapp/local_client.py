"""
Local WhatsApp Web transport — DEVELOPMENT ONLY.

Connects a personal WhatsApp number over the multi-device protocol (via
`neonize`, which wraps whatsmeow) so the concierge can be exercised end to end
without a Meta Business account, an approved number, or a public webhook.

⚠️ NOT FOR PRODUCTION, for two reasons that are not going away:

  1. It is UNOFFICIAL. WhatsApp does not sanction third-party clients and can
     ban the number. Scan a spare number, never one tied to a business account.
  2. It bypasses the webhook, so there is no `X-Hub-Signature-256` to verify —
     the security boundary the Cloud API path depends on does not exist here.
     Worse, a linked device receives EVERY message that number gets, and
     `core/authz.is_authorized_user` currently has no callers, so anyone who
     texts the number reaches the concierge. Wire the allowlist before linking
     a number other people message.

WHY NOT OpenWA, WHICH WAS ASKED FOR: the Python port (`openwa` on PyPI) last
shipped in November 2021 and drives a WhatsApp Web UI that has been rebuilt
several times since; it does not connect. Real OpenWA is a Node application
needing a sidecar process and bundled Chromium. `neonize` speaks the protocol
directly — one dependency, one process, no browser.

INTERFACE: this module exposes the same names the rest of the app already
imports from `whatsapp`. Nothing above the transport can tell which one is
running. See `whatsapp/__init__.py`.

Session lives in .data/whatsapp-session/ — gitignored. Scan the QR once; it
survives restarts until you log out from your phone.
"""
import asyncio
import os
import sys
import threading
from collections import namedtuple
from pathlib import Path

from core.logger import logger

# canonical_phone lives with the Cloud API transport but is not Meta-specific —
# it is THE phone format for the whole system. Importing it keeps one
# definition, so both transports key users identically.
from whatsapp.cloud_api import SendErrorClass, canonical_phone

SESSION_DIR = Path(os.getenv("WHATSAPP_SESSION_DIR", ".data/whatsapp-session"))
SESSION_DB = SESSION_DIR / "session.db"

# WhatsApp's own server for one-to-one chats. Groups are a different server and
# are ignored — the concierge is a private conversation by design.
DIRECT_SERVER = "s.whatsapp.net"

_client = None
_thread = None
_loop = None            # the app's event loop, for the listener thread to post into
_connected = threading.Event()
_qr_count = 0          # QR codes rotate; numbering them makes that visible
_paired = threading.Event()


class NotConfigured(RuntimeError):
    """Kept for interface parity with the Cloud API transport. This one needs no
    credentials, so it is raised only when the session is not connected yet."""


def is_configured() -> bool:
    """True once the QR has been scanned and the socket is up."""
    return _connected.is_set()


def api_version() -> str:
    return "local-whatsapp-web"


# ----------------------------------------------------------------------
# Message extraction
# ----------------------------------------------------------------------
def message_text(message) -> str:
    """The user's words, whichever field WhatsApp used.

    Plain messages arrive as `conversation`; replies and anything with
    formatting or a link arrive as `extendedTextMessage.text`. Reading only the
    first drops every reply — which is most of a real conversation.
    """
    if message is None:
        return ""
    if getattr(message, "conversation", ""):
        return message.conversation
    extended = getattr(message, "extendedTextMessage", None)
    if extended is not None and getattr(extended, "text", ""):
        return extended.text
    # Button and list replies: the chosen title IS what the user said.
    for field in ("buttonsResponseMessage", "listResponseMessage",
                  "templateButtonReplyMessage"):
        reply = getattr(message, field, None)
        if reply is None:
            continue
        for attr in ("selectedDisplayText", "title", "selectedButtonID", "selectedID"):
            value = getattr(reply, attr, "")
            if value:
                return value
    return ""


def parse_event(event) -> dict | None:
    """Turn a neonize MessageEv into the same shape the webhook produces.

    Returns None for anything the concierge must not answer: our own outgoing
    messages, group chats, and events with no sender. Mirrors
    `cloud_api.parse_inbound` field for field so the worker cannot tell them
    apart.
    """
    info = getattr(event, "Info", None)
    source = getattr(info, "MessageSource", None) if info is not None else None
    if source is None:
        return None

    # Echoes of our OWN replies come back through the same stream. Answering
    # them would put the concierge in a conversation with itself.
    if getattr(source, "IsFromMe", False):
        return None
    if getattr(source, "IsGroup", False):
        return None

    sender = getattr(source, "Sender", None)
    phone = canonical_phone(getattr(sender, "User", "") if sender else "")
    if not phone:
        return None

    body = message_text(getattr(event, "Message", None))
    return {
        "message_id": str(getattr(info, "ID", "") or ""),
        "phone": phone,
        "body": body,
        # No text means media, a sticker, a location — same honest handling as
        # the Cloud API path: keep it so the worker can say it can't read it.
        "unsupported": not body,
        "type": "text" if body else "unsupported",
    }


# ----------------------------------------------------------------------
# Error classification
# ----------------------------------------------------------------------
def classify_send_error(exc) -> SendErrorClass:
    """Retryable or permanent, in the same shape the worker already handles.

    There are no HTTP status codes here — a local socket either delivered or it
    did not — so almost everything is worth retrying. The exception is not being
    connected, which no number of retries fixes.
    """
    if isinstance(exc, NotConfigured):
        return SendErrorClass(False, None, "WhatsApp Web session is not connected")
    return SendErrorClass(True, None, f"local WhatsApp send failed ({type(exc).__name__})")


# ----------------------------------------------------------------------
# Outbound
# ----------------------------------------------------------------------
def _require_client():
    if _client is None or not _connected.is_set():
        raise NotConfigured(
            "WhatsApp Web is not connected. Start the app and scan the QR code."
        )
    return _client


async def send_text(to: str, body: str) -> str:
    """Send a text message. Returns the provider message id.

    The underlying call is blocking, so it runs in a thread — otherwise one slow
    send would stall every other phone's worker.
    """
    from neonize.utils import build_jid

    client = _require_client()
    recipient = canonical_phone(to)
    response = await asyncio.to_thread(
        client.send_message, build_jid(recipient, DIRECT_SERVER), body
    )
    message_id = str(getattr(response, "ID", "") or "")
    logger.info(f"[whatsapp] sent text to {recipient} — message_id={message_id} "
                f"({len(body)} chars)")
    return message_id


async def mark_read(message_id: str) -> bool:
    """Best effort read receipt. Never raises — a receipt must not affect a reply."""
    if not message_id:
        return False
    logger.debug(f"[whatsapp] mark_read({message_id}) not sent — the local "
                 f"transport needs the chat JID, which the worker does not carry")
    return False


# Media and templates are Cloud API concepts. Declaring them here so an
# accidental call fails loudly in development rather than silently doing
# nothing, and so the interface gap is obvious rather than discovered later.
async def send_image(to: str, link: str, caption: str = None) -> str:
    raise NotImplementedError("send_image is Cloud API only; not needed for local testing")


async def send_document(to: str, link: str, filename: str = None, caption: str = None) -> str:
    raise NotImplementedError("send_document is Cloud API only; not needed for local testing")


async def send_template(to: str, name: str, language: str = "en_US", components: list = None) -> str:
    raise NotImplementedError(
        "Templates are a Meta Business feature. The 24-hour window does not "
        "apply to a personal WhatsApp Web session."
    )


# ----------------------------------------------------------------------
# Lifecycle
# ----------------------------------------------------------------------
def _on_message(_, event):
    """Hand an inbound message to the EXISTING worker pipeline.

    Runs on neonize's own thread, so it posts into the app's event loop rather
    than touching the database here. Deliberately does no AI work, no parsing
    beyond normalisation, and no ordering logic — this is a transport.
    """
    from backend.whatsapp_worker import enqueue_and_wake

    try:
        message = parse_event(event)
    except Exception:
        logger.error("[whatsapp] could not parse an inbound event", exc_info=True)
        return
    if message is None:
        return

    logger.info(f"[whatsapp] received from {message['phone']} "
                f"(message_id={message['message_id']})")

    future = asyncio.run_coroutine_threadsafe(
        enqueue_and_wake(
            message_sid=message["message_id"],
            phone=message["phone"],
            body=message["body"],
            num_media=1 if message["unsupported"] else 0,
        ),
        _loop,
    )

    def report(done):
        try:
            _, is_new = done.result()
            if not is_new:
                logger.info(f"[whatsapp] duplicate ignored ({message['message_id']})")
        except Exception:
            logger.error("[whatsapp] enqueue failed", exc_info=True)

    future.add_done_callback(report)


def _print_qr(_, qr_bytes):
    """Show the pairing QR. Scanned once; the session then persists.

    This runs inside a ctypes callback, where an exception is swallowed by the
    runtime and merely printed — so a failure here means NO QR appears and the
    login silently never happens. Everything is guarded, and a PNG is always
    written as a fallback.
    """
    global _qr_count
    _qr_count += 1
    payload = qr_bytes.decode()
    print(
        f"\n  ── QR #{_qr_count} — WhatsApp > Settings > Linked devices > Link a device"
        f"\n     Each code is valid ~20s and pairing gives up after about a minute."
        f"\n     Have the camera open BEFORE you start the app.\n",
        flush=True,
    )

    try:
        import io

        import qrcode

        code = qrcode.QRCode(border=2)
        code.add_data(payload)

        # The Windows console is cp1252 and cannot encode the block glyphs, so
        # writing through the text layer raises and the QR never appears.
        # Render to a buffer and push the bytes out as UTF-8 directly.
        buffer = io.StringIO()
        code.print_ascii(out=buffer, invert=True)
        sys.stdout.buffer.write(buffer.getvalue().encode("utf-8", errors="replace"))
        sys.stdout.buffer.flush()

        image_path = SESSION_DIR / "qr.png"
        code.make_image().save(image_path)
        print(f"\n  Also saved as an image: {image_path}\n", flush=True)
    except Exception as e:
        # Never let QR rendering take down the connection.
        logger.warning(f"[whatsapp] could not render the QR ({e!r}). Raw pairing "
                       f"payload below — paste it into any QR generator:\n{payload}")


async def start() -> None:
    """Connect, and keep the socket alive on a background thread.

    Returns as soon as the connection thread is running. The QR — if one is
    needed — is printed by the callback above.
    """
    global _client, _thread, _loop
    if _thread is not None:
        return

    from neonize.client import NewClient
    from neonize.events import ConnectedEv, DisconnectedEv, MessageEv, PairStatusEv

    SESSION_DIR.mkdir(parents=True, exist_ok=True)
    _loop = asyncio.get_running_loop()
    _client = NewClient(str(SESSION_DB))

    @_client.event(MessageEv)
    def handle_message(client, event):        # noqa: F811 — neonize dispatch
        _on_message(client, event)

    @_client.event(ConnectedEv)
    def handle_connected(client, event):      # noqa: F811
        _connected.set()
        logger.info("[whatsapp] connected to WhatsApp Web")

    @_client.event(DisconnectedEv)
    def handle_disconnected(client, event):   # noqa: F811
        # neonize reconnects on its own; flip the flag so sends fail fast and
        # the worker retries rather than hanging on a dead socket.
        _connected.clear()
        if _qr_count and not _paired.is_set():
            # Disconnected while still showing QR codes = nobody scanned in time.
            # "Login event: timeout" on its own is not an actionable message.
            logger.warning(
                "[whatsapp] PAIRING TIMED OUT — the QR expired before it was "
                "scanned. Restart the app with the camera already open on "
                "WhatsApp > Settings > Linked devices > Link a device."
            )
        else:
            logger.warning("[whatsapp] disconnected — neonize will retry")

    @_client.event(PairStatusEv)
    def handle_paired(client, event):         # noqa: F811
        _paired.set()
        logger.info(f"[whatsapp] device paired — session saved to {SESSION_DB}. "
                    f"You will not need to scan again until you log out from the phone.")

    _client.event.qr(_print_qr)

    def run():
        try:
            _client.connect()
        except Exception:
            logger.error("[whatsapp] connection thread died", exc_info=True)
            _connected.clear()

    _thread = threading.Thread(target=run, name="whatsapp-web", daemon=True)
    _thread.start()
    logger.warning(
        "[whatsapp] LOCAL DEVELOPMENT TRANSPORT — unofficial WhatsApp Web "
        "client. Not for production; the number can be banned. "
        f"Session: {SESSION_DB}"
    )


async def stop() -> None:
    global _client, _thread
    if _client is not None:
        try:
            _client.disconnect()
        except Exception:
            logger.info("[whatsapp] disconnect failed on shutdown", exc_info=True)
    _connected.clear()
    _client, _thread = None, None
