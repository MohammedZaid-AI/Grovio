"""
The local WhatsApp Web transport (WHATSAPP_PROVIDER=local).

What matters is that nothing ABOVE the transport can tell which one is running:
the same function names, the same message shape, the same canonical phone key,
and the same entry point into the existing worker.

No network and no WhatsApp session. neonize's protobufs are real — the event
objects here are built from the same classes the library hands us — but nothing
connects.
"""
import asyncio
import os
import sys
import tempfile

sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ["MCP_USE_ANONYMIZED_TELEMETRY"] = "false"
os.environ.setdefault("WHATSAPP_ACCESS_TOKEN", "test-token")
os.environ.setdefault("WHATSAPP_PHONE_NUMBER_ID", "555000111")

import db

_tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
_tmp.close()
db.DB_PATH = _tmp.name
db.init_db()

from whatsapp import cloud_api, local_client

_passed = _failed = 0


def check(name, condition):
    global _passed, _failed
    if condition:
        _passed += 1
        print(f"  ✅ {name}")
    else:
        _failed += 1
        print(f"  ❌ {name}")


def run(coro):
    return asyncio.run(coro)


try:
    from neonize.proto import Neonize_pb2 as npb
    from neonize.proto.waE2E import WAWebProtobufsE2E_pb2 as e2e
    HAVE_NEONIZE = True
except ImportError:
    HAVE_NEONIZE = False


def event(text=None, sender="917795871481", from_me=False, is_group=False,
          message_id="LOCAL-1", extended=None):
    """A real neonize MessageEv, built the way the library delivers one."""
    ev = npb.Message()
    ev.Info.ID = message_id
    ev.Info.MessageSource.IsFromMe = from_me
    ev.Info.MessageSource.IsGroup = is_group
    ev.Info.MessageSource.Sender.User = sender
    ev.Info.MessageSource.Sender.Server = "s.whatsapp.net"
    if text is not None:
        ev.Message.conversation = text
    if extended is not None:
        ev.Message.extendedTextMessage.text = extended
    return ev


# ======================================================================
print("\n[1] Provider selection")
os.environ["WHATSAPP_PROVIDER"] = "cloud"
import importlib

import whatsapp

whatsapp = importlib.reload(whatsapp)
check("cloud is selected by name", whatsapp.PROVIDER == "cloud")
check("and binds the Cloud API sender", whatsapp.send_text is cloud_api.send_text)

os.environ["WHATSAPP_PROVIDER"] = "local"
whatsapp = importlib.reload(whatsapp)
check("local is selected by name", whatsapp.PROVIDER == "local")
check("and binds the local sender", whatsapp.send_text is local_client.send_text)
check("local reports its own api_version",
      whatsapp.api_version() == "local-whatsapp-web")

os.environ.pop("WHATSAPP_PROVIDER")
whatsapp = importlib.reload(whatsapp)
check("cloud is the DEFAULT — production is never opt-in", whatsapp.PROVIDER == "cloud")

os.environ["WHATSAPP_PROVIDER"] = "openwa"
try:
    importlib.reload(whatsapp)
    check("an unknown provider fails LOUDLY, not silently", False)
except RuntimeError as e:
    check("an unknown provider fails LOUDLY, not silently", "not a transport" in str(e))

os.environ["WHATSAPP_PROVIDER"] = "local"
whatsapp = importlib.reload(whatsapp)

# ======================================================================
print("\n[2] The interface is identical either side")
for name in ("send_text", "send_image", "send_document", "send_template",
             "mark_read", "classify_send_error", "is_configured", "api_version",
             "canonical_phone", "NotConfigured", "SendErrorClass"):
    check(f"local exposes {name}", hasattr(local_client, name))

# Whatever the worker and routes import must resolve under BOTH providers.
for name in ("send_text", "classify_send_error", "verify_signature",
             "parse_inbound", "parse_statuses", "verify_token_matches",
             "canonical_phone", "start", "stop"):
    check(f"whatsapp.{name} resolves in local mode", hasattr(whatsapp, name))

check("webhook helpers still come from the Cloud API module",
      whatsapp.verify_signature is cloud_api.verify_signature)

# ======================================================================
print("\n[3] Canonical phone numbers — ONE user, not two")
check("a JID user part is already canonical",
      local_client.canonical_phone("917795871481") == "917795871481")
check("a full JID string is normalised",
      local_client.canonical_phone("917795871481@s.whatsapp.net") == "917795871481")
check("the legacy Twilio format still normalises",
      local_client.canonical_phone("whatsapp:+917795871481") == "917795871481")
check("BOTH transports agree exactly",
      local_client.canonical_phone("917795871481@c.us")
      == cloud_api.canonical_phone("917795871481"))
check("db agrees too, so no duplicate user is created",
      db._canonical_phone("917795871481@s.whatsapp.net")
      == local_client.canonical_phone("917795871481@s.whatsapp.net"))

if not HAVE_NEONIZE:
    print("\n  ⚠️  neonize is not installed — skipping the event-parsing sections.")
    print("     pip install neonize qrcode")
else:
    # ==================================================================
    print("\n[4] Incoming message conversion")
    parsed = local_client.parse_event(event("I need milk"))
    check("a text message parses", parsed is not None)
    check("body extracted", parsed["body"] == "I need milk")
    check("message id extracted", parsed["message_id"] == "LOCAL-1")
    check("phone canonicalised from the JID", parsed["phone"] == "917795871481")
    check("text is not flagged unsupported", parsed["unsupported"] is False)
    check("the shape MATCHES the Cloud API's",
          set(parsed) == {"message_id", "phone", "body", "unsupported", "type"})

    replies = local_client.parse_event(event(text=None, extended="the second one"))
    check("REGRESSION GUARD: a reply (extendedTextMessage) is read, not dropped",
          replies["body"] == "the second one")

    media = local_client.parse_event(event(text=None))
    check("a message with no text is kept", media is not None)
    check("and flagged unsupported so the bot can say so", media["unsupported"] is True)

    print("\n[5] What must NEVER reach the concierge")
    check("our OWN outgoing messages are ignored — no self-conversation",
          local_client.parse_event(event("hello", from_me=True)) is None)
    check("group chats are ignored", local_client.parse_event(event("hi", is_group=True)) is None)
    check("an event with no sender is ignored",
          local_client.parse_event(event("hi", sender="")) is None)
    check("a malformed event is ignored, not raised",
          local_client.parse_event(object()) is None)

    # ==================================================================
    print("\n[5b] LID addressing — a long id is NOT a phone number")
    # Observed live: a message arrived from 81209342308582@lid. Reading Sender
    # blindly keyed the user by that LID and replied to
    # 81209342308582@s.whatsapp.net, which nobody owns — the send returned an
    # id so it LOOKED delivered, but the recipient never got it.
    def source(sender_user, sender_server, alt_user=None, chat_user=None,
               chat_server="s.whatsapp.net"):
        ev = npb.Message()
        ev.Info.ID = "LID-1"
        ev.Info.MessageSource.Sender.User = sender_user
        ev.Info.MessageSource.Sender.Server = sender_server
        if alt_user:
            ev.Info.MessageSource.SenderAlt.User = alt_user
            ev.Info.MessageSource.SenderAlt.Server = "s.whatsapp.net"
        ev.Info.MessageSource.Chat.User = chat_user or sender_user
        ev.Info.MessageSource.Chat.Server = chat_server
        ev.Message.conversation = "hi"
        return ev

    check("a plain phone sender resolves to its number",
          local_client._sender_phone(
              source("917795871481", "s.whatsapp.net").Info.MessageSource)
          == "917795871481")
    check("REGRESSION: a LID sender resolves via SenderAlt, not the LID",
          local_client._sender_phone(
              source("81209342308582", "lid", alt_user="917795871481").Info.MessageSource)
          == "917795871481")
    check("a LID with no phone alternative is DROPPED, not made into a user",
          local_client._sender_phone(
              source("81209342308582", "lid").Info.MessageSource) == "")
    check("and the whole event is ignored",
          local_client.parse_event(source("81209342308582", "lid")) is None)
    check("a LID id never becomes a phone key",
          local_client.parse_event(
              source("81209342308582", "lid", alt_user="917795871481"))["phone"]
          == "917795871481")

    local_client._chat_jids.clear()
    local_client.parse_event(source("81209342308582", "lid",
                                    alt_user="917795871481",
                                    chat_user="81209342308582", chat_server="lid"))
    check("the chat JID is remembered so the reply lands in the right chat",
          local_client._chat_jids.get("917795871481") == ("81209342308582", "lid"))
    local_client._chat_jids.clear()

    print("\n[6] Text extraction covers the real shapes")
    m = e2e.Message()
    m.conversation = "plain"
    check("conversation", local_client.message_text(m) == "plain")
    m = e2e.Message()
    m.extendedTextMessage.text = "a reply"
    check("extendedTextMessage", local_client.message_text(m) == "a reply")
    m = e2e.Message()
    m.buttonsResponseMessage.selectedDisplayText = "1"
    check("button reply becomes its title", local_client.message_text(m) == "1")
    check("None is safe", local_client.message_text(None) == "")
    check("an empty message yields empty", local_client.message_text(e2e.Message()) == "")

# ======================================================================
print("\n[7] Outgoing messages fail SAFE when not connected")
local_client._connected.clear()
local_client._client = None
try:
    run(local_client.send_text("917795871481", "hi"))
    check("sending without a session raises NotConfigured", False)
except local_client.NotConfigured:
    check("sending without a session raises NotConfigured", True)

check("not connected reports unconfigured", local_client.is_configured() is False)
check("mark_read never raises", run(local_client.mark_read("X")) is False)
check("mark_read with no id is a no-op", run(local_client.mark_read("")) is False)

for name in ("send_image", "send_document", "send_template"):
    try:
        run(getattr(local_client, name)("917795871481", "x"))
        check(f"{name} fails loudly rather than silently doing nothing", False)
    except NotImplementedError:
        check(f"{name} fails loudly rather than silently doing nothing", True)
    except TypeError:
        check(f"{name} fails loudly rather than silently doing nothing", True)

# ======================================================================
print("\n[8] Send-error classification the worker already understands")
result = local_client.classify_send_error(local_client.NotConfigured("no session"))
check("no session is PERMANENT — retrying cannot connect", result.retryable is False)
check("and says why", "not connected" in result.reason)

result = local_client.classify_send_error(ConnectionResetError("socket died"))
check("a dropped socket is RETRYABLE", result.retryable is True)
check("the shape matches the Cloud API's SendErrorClass",
      isinstance(result, cloud_api.SendErrorClass)
      and set(result._fields) == {"retryable", "code", "reason"})

# ======================================================================
print("\n[9] Inbound feeds the EXISTING worker, unchanged")
import backend.whatsapp_worker as worker

check("the worker's entry point is untouched", hasattr(worker, "enqueue_and_wake"))

calls = []


async def fake_enqueue(message_sid, phone, body, num_media=0):
    calls.append({"message_sid": message_sid, "phone": phone,
                  "body": body, "num_media": num_media})
    return 1, True


async def deliver():
    """Drive _on_message the way neonize does: from another thread."""
    import threading

    local_client._loop = asyncio.get_running_loop()
    original = worker.enqueue_and_wake
    worker.enqueue_and_wake = fake_enqueue
    try:
        done = threading.Event()

        def from_neonize_thread():
            local_client._on_message(None, event("I want biryani"))
            done.set()

        threading.Thread(target=from_neonize_thread).start()
        await asyncio.sleep(0.25)
        done.wait(timeout=2)
        await asyncio.sleep(0.05)
    finally:
        worker.enqueue_and_wake = original


if HAVE_NEONIZE:
    run(deliver())
    check("the message reached the worker", len(calls) == 1)
    check("under the canonical phone key", calls and calls[0]["phone"] == "917795871481")
    check("with the body intact", calls and calls[0]["body"] == "I want biryani")
    check("carrying the provider message id for DEDUP",
          calls and calls[0]["message_sid"] == "LOCAL-1")

    calls.clear()

    async def deliver_own():
        import threading
        local_client._loop = asyncio.get_running_loop()
        original = worker.enqueue_and_wake
        worker.enqueue_and_wake = fake_enqueue
        try:
            threading.Thread(target=lambda: local_client._on_message(
                None, event("my own reply", from_me=True))).start()
            await asyncio.sleep(0.25)
        finally:
            worker.enqueue_and_wake = original

    run(deliver_own())
    check("our own reply never reaches the worker", calls == [])

# ======================================================================
print("\n[10] No AI logic lives in the transport")
import pathlib

import io
import tokenize

source = pathlib.Path(local_client.__file__).read_text(encoding="utf-8")

# Tokenise, don't grep: the docstring legitimately explains what the transport
# hands off to and which safeguards still apply. What must not exist is CODE
# reaching into the AI layer.
identifiers = {
    token.string.lower()
    for token in tokenize.generate_tokens(io.StringIO(source).readline)
    if token.type == tokenize.NAME
}
for forbidden in ("concierge", "planner", "skills", "llm", "openai", "groq",
                  "recommendation", "registry", "swiggy", "respond", "plan"):
    check(f"no code reference to {forbidden}", forbidden not in identifiers)

check("it only touches the worker's public entry point",
      "enqueue_and_wake" in identifiers and "_worker_loop" not in identifiers)
check("it does not import the AI layer", "\nfrom ai" not in source and "\nimport ai" not in source)

# ======================================================================
print("\n[11] Security boundaries hold")
check("the session path is gitignored",
      ".data/" in pathlib.Path(".gitignore").read_text(encoding="utf-8"))
check("no credential is hardcoded",
      "password" not in source.lower() and "secret" not in source.lower().replace("secrets", ""))
check("the transport never READS the allowlist — authorisation stays above it",
      "AUTHORIZED_PHONES" not in identifiers and "is_authorized_user" not in identifiers)

from core import authz

check("AUTHORIZED_PHONES still gates spending", hasattr(authz, "is_authorized_user"))
os.environ["AUTHORIZED_PHONES"] = "917795871481"
check("an allowed number passes", authz.is_authorized_user("917795871481") is True)
check("a stranger is denied", authz.is_authorized_user("910000000000") is False)
os.environ["AUTHORIZED_PHONES"] = ""
check("FAILS CLOSED with no allowlist", authz.is_authorized_user("917795871481") is False)

# ======================================================================
print("[12] A failed dial must not kill the transport for good")
# Observed live: "failed to WebSocket dial: TLS handshake timeout" on the first
# connect. The thread caught it, logged, and exited. The app still said "startup
# complete" and then silently answered nobody — indistinguishable from the bot
# being broken. neonize retries once a socket is up; getting there is on us.
import inspect
import whatsapp.local_client as lc

check("there is a ceiling on the backoff, so logs stay readable",
      lc.RECONNECT_MAX_SECONDS >= lc.RECONNECT_SECONDS > 0)

source = inspect.getsource(lc)
run_body = source[source.index("    def run():"):source.index("connection thread stopped")]
check("the dial is retried, not attempted once",
      "while " in run_body and "_client.connect()" in run_body)
check("the backoff actually waits", "time.sleep" in run_body)
check("an unlinked device stops the retry — the session is dead",
      "_unlinked.is_set()" in run_body)
check("being logged out sets that flag",
      "_unlinked.set()" in source)
check("and a fresh start clears it", "_unlinked.clear()" in source)

delays = []
attempt = 0
for _ in range(8):
    attempt += 1
    delays.append(min(lc.RECONNECT_MAX_SECONDS,
                      lc.RECONNECT_SECONDS * 2 ** min(attempt - 1, 4)))
check("backoff grows then holds", delays[0] < delays[3] and delays[-1] == lc.RECONNECT_MAX_SECONDS)
check("and never retries in a tight loop", min(delays) >= 1)

# ======================================================================
print("[13] REGRESSION: plumbing is not an attachment")
# Observed live: a user said "hi" and got "I can't open attachments yet" after
# EVERY message. Each ordinary text also arrives as one or more bodyless events
# (senderKeyDistributionMessage, messageContextInfo, protocolMessage), and
# "no text" was being read as "must be media".


class Payload:
    def __init__(self, **fields):
        self.conversation = fields.pop("conversation", "")
        for name, value in fields.items():
            setattr(self, name, value)

    def __getattr__(self, name):
        return None


def event_for(payload):
    source = type("S", (), {"IsFromMe": False, "IsGroup": False,
                            "Sender": type("J", (), {"User": "919000000001",
                                                     "Server": "s.whatsapp.net"})(),
                            "Chat": type("J", (), {"User": "919000000001",
                                                   "Server": "s.whatsapp.net"})()})()
    info = type("I", (), {"ID": "MSG-1", "MessageSource": source})()
    return type("E", (), {"Info": info, "MessageSource": source, "Message": payload})()


text_event = lc.parse_event(event_for(Payload(conversation="hi")))
check("a text message is a text message", text_event["body"] == "hi")
check("and is never flagged as an attachment", text_event["unsupported"] is False)

key_event = lc.parse_event(event_for(Payload(senderKeyDistributionMessage=object())))
check("a key-distribution event is dropped entirely", key_event is None)
check("a bare context event is dropped too",
      lc.parse_event(event_for(Payload(messageContextInfo=object()))) is None)
check("so is an empty payload", lc.parse_event(event_for(Payload())) is None)

photo = lc.parse_event(event_for(Payload(imageMessage=object())))
check("a real photo still reaches the worker", photo is not None)
check("and is honestly reported as unreadable", photo["unsupported"] is True)
check("a voice note as well",
      lc.parse_event(event_for(Payload(audioMessage=object())))["unsupported"] is True)

print("\n" + "=" * 70)
print(f"RESULT: {_passed} passed, {_failed} failed")
try:
    os.unlink(_tmp.name)
except OSError:
    pass
sys.exit(1 if _failed else 0)
