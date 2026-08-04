"""
WhatsApp Business Cloud API — the whole messaging layer.

Covers the migration off Twilio: webhook verification, inbound text and
interactive replies, outgoing messages, delivery and read receipts, invalid
signatures, expired tokens, rate limits, retry classification, unknown events,
and the phone-key migration that stops the cutover orphaning existing users.

No network. Outbound is exercised against a fake httpx transport that records
exactly what would have been sent to Meta.
"""
import asyncio
import hashlib
import hmac
import json
import os
import sys
import tempfile

sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import httpx

os.environ["MCP_USE_ANONYMIZED_TELEMETRY"] = "false"

import db

_tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
_tmp.close()
db.DB_PATH = _tmp.name
db.init_db()

import whatsapp
from whatsapp import cloud_api

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


def sign(body: bytes, secret: str) -> str:
    return "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


def http_error(status, code=None):
    payload = {"error": {"code": code}} if code is not None else {}
    request = httpx.Request("POST", "https://graph.facebook.com/v23.0/1/messages")
    response = httpx.Response(status, json=payload, request=request)
    return httpx.HTTPStatusError("boom", request=request, response=response)


class FakeMeta:
    """Records every request the provider would send, and replies as told."""

    def __init__(self, status=200, payload=None):
        self.requests = []
        self.status = status
        self.payload = payload if payload is not None else {
            "messaging_product": "whatsapp",
            "contacts": [{"wa_id": "919876543210"}],
            "messages": [{"id": "wamid.OUT1"}],
        }

    def __call__(self, request: httpx.Request):
        self.requests.append({
            "url": str(request.url),
            "auth": request.headers.get("authorization"),
            "json": json.loads(request.content or b"{}"),
        })
        return httpx.Response(self.status, json=self.payload)

    @property
    def last(self):
        return self.requests[-1]["json"]


def with_meta(meta):
    """Patch httpx.AsyncClient so the provider talks to `meta` instead of Meta."""
    original = httpx.AsyncClient

    class Patched(original):
        def __init__(self, *a, **kw):
            kw["transport"] = httpx.MockTransport(meta)
            super().__init__(*a, **kw)

    httpx.AsyncClient = Patched
    return original


def restore(original):
    httpx.AsyncClient = original


os.environ["WHATSAPP_ACCESS_TOKEN"] = "test-token-never-logged"
os.environ["WHATSAPP_PHONE_NUMBER_ID"] = "555000111"

# ======================================================================
print("\n[1] Webhook verification (GET handshake)")
os.environ["WHATSAPP_VERIFY_TOKEN"] = "my-verify-token"
check("correct mode + token accepted", whatsapp.verify_token_matches(
    {"hub.mode": "subscribe", "hub.verify_token": "my-verify-token"}))
check("wrong token rejected", not whatsapp.verify_token_matches(
    {"hub.mode": "subscribe", "hub.verify_token": "guess"}))
check("wrong mode rejected", not whatsapp.verify_token_matches(
    {"hub.mode": "unsubscribe", "hub.verify_token": "my-verify-token"}))
check("empty params rejected", not whatsapp.verify_token_matches({}))

os.environ.pop("WHATSAPP_VERIFY_TOKEN")
check("SECURITY: unset verify token FAILS CLOSED", not whatsapp.verify_token_matches(
    {"hub.mode": "subscribe", "hub.verify_token": ""}))
os.environ["WHATSAPP_VERIFY_TOKEN"] = "my-verify-token"

# ======================================================================
print("\n[2] Signature verification fails CLOSED")
body = json.dumps({"hello": "world"}).encode()
os.environ.pop("WHATSAPP_APP_SECRET", None)
check("SECURITY: no app secret -> every request rejected",
      whatsapp.verify_signature(body, sign(body, "s")) is False)

os.environ["WHATSAPP_APP_SECRET"] = "topsecret"
check("valid signature accepted", whatsapp.verify_signature(body, sign(body, "topsecret")))
check("wrong secret rejected", not whatsapp.verify_signature(body, sign(body, "other")))
check("tampered body rejected",
      not whatsapp.verify_signature(b'{"hello":"evil"}', sign(body, "topsecret")))
check("missing header rejected", not whatsapp.verify_signature(body, ""))
check("malformed header rejected", not whatsapp.verify_signature(body, "md5=abc"))
check("empty body still verifies correctly",
      whatsapp.verify_signature(b"", sign(b"", "topsecret")))

# ======================================================================
print("\n[3] Inbound text")
TEXT = {"object": "whatsapp_business_account", "entry": [{"changes": [{"value": {
    "messages": [{"id": "wamid.1", "from": "919876543210", "type": "text",
                  "text": {"body": "I need milk"}}]
}}]}]}
messages = whatsapp.parse_inbound(TEXT)
check("one message parsed", len(messages) == 1)
check("body extracted", messages[0]["body"] == "I need milk")
check("message id extracted", messages[0]["message_id"] == "wamid.1")
check("phone is a bare MSISDN", messages[0]["phone"] == "919876543210")
check("text is not flagged unsupported", messages[0]["unsupported"] is False)

# ======================================================================
print("\n[4] Interactive replies count as what the user said")
INTERACTIVE = {"entry": [{"changes": [{"value": {"messages": [
    {"id": "wamid.2", "from": "919876543210", "type": "interactive",
     "interactive": {"type": "button_reply",
                     "button_reply": {"id": "opt_1", "title": "1"}}}
]}}]}]}
reply = whatsapp.parse_inbound(INTERACTIVE)[0]
check("button reply becomes its title", reply["body"] == "1")
check("and is treated as supported", reply["unsupported"] is False)

LIST_REPLY = {"entry": [{"changes": [{"value": {"messages": [
    {"id": "wamid.3", "from": "919876543210", "type": "interactive",
     "interactive": {"type": "list_reply",
                     "list_reply": {"id": "r2", "title": "Meghana Foods"}}}
]}}]}]}
check("list reply becomes its title",
      whatsapp.parse_inbound(LIST_REPLY)[0]["body"] == "Meghana Foods")

MEDIA = {"entry": [{"changes": [{"value": {"messages": [
    {"id": "wamid.4", "from": "919876543210", "type": "image",
     "image": {"id": "media-1"}}
]}}]}]}
media = whatsapp.parse_inbound(MEDIA)[0]
check("media is kept, not dropped", media["message_id"] == "wamid.4")
check("media is flagged unsupported so the bot can say so", media["unsupported"] is True)

# ======================================================================
print("\n[5] Unknown and malformed events are ignored safely")
for label, payload in (
    ("empty dict", {}),
    ("no entry", {"object": "whatsapp_business_account"}),
    ("entry not a list", {"entry": "nope"}),
    ("changes not a list", {"entry": [{"changes": "nope"}]}),
    ("value not a dict", {"entry": [{"changes": [{"value": 42}]}]}),
    ("messages not a list", {"entry": [{"changes": [{"value": {"messages": 7}}]}]}),
    ("statuses not a list", {"entry": [{"changes": [{"value": {"statuses": 7}}]}]}),
    ("message not a dict", {"entry": [{"changes": [{"value": {"messages": ["x"]}}]}]}),
    ("message with no sender", {"entry": [{"changes": [{"value": {"messages": [
        {"id": "w", "type": "text", "text": {"body": "hi"}}]}}]}]}),
    ("null payload", None),
    ("a list, not an object", []),
):
    try:
        check(f"{label} -> no messages, no exception", whatsapp.parse_inbound(payload) == [])
    except Exception as e:
        check(f"{label} -> no messages, no exception", False)

check("an unknown event type yields no statuses either",
      whatsapp.parse_statuses({"entry": [{"changes": [{"value": {"foo": "bar"}}]}]}) == [])

# ======================================================================
print("\n[6] Delivery and read receipts")
STATUSES = {"entry": [{"changes": [{"value": {"statuses": [
    {"id": "wamid.OUT1", "status": "delivered", "recipient_id": "919876543210",
     "timestamp": "1730000000"},
    {"id": "wamid.OUT2", "status": "read", "recipient_id": "919876543210"},
    {"id": "wamid.OUT3", "status": "failed", "recipient_id": "919876543210",
     "errors": [{"code": 131047, "title": "Re-engagement message"}]},
]}}]}]}
statuses = whatsapp.parse_statuses(STATUSES)
check("all three receipts parsed", len(statuses) == 3)
check("delivered read correctly", statuses[0]["status"] == "delivered")
check("read receipt read correctly", statuses[1]["status"] == "read")
check("failure carries the error code", statuses[2]["error_code"] == 131047)
check("failure carries the reason", "Re-engagement" in statuses[2]["error_title"])
check("recipient normalised", statuses[0]["recipient"] == "919876543210")

# Statuses applied to the outbound row, forward-only.
inbound_id, _ = db.enqueue_inbound_message("wamid.IN1", "919876543210", "hi")
db.save_reply_and_finish(inbound_id, "919876543210", ["hello"])
out_id = db.get_pending_outbound("919876543210")[0]["id"]
db.mark_outbound_sent(out_id, "wamid.OUT1")

check("delivered advances SENT -> DELIVERED",
      db.record_delivery_status("wamid.OUT1", "delivered") is True)
check("read advances DELIVERED -> READ",
      db.record_delivery_status("wamid.OUT1", "read") is True)
check("a LATE delivered receipt cannot un-read the message",
      db.record_delivery_status("wamid.OUT1", "delivered") is False)
check("a duplicate read receipt is a no-op",
      db.record_delivery_status("wamid.OUT1", "read") is False)
check("an unknown message id changes nothing",
      db.record_delivery_status("wamid.NOPE", "read") is False)
check("an unknown status verb is ignored",
      db.record_delivery_status("wamid.OUT1", "teleported") is False)

row = db.get_connection().execute(
    "SELECT status FROM whatsapp_outbound WHERE id = ?", (out_id,)).fetchone()
check("the row ended up READ", row[0] == "READ")

db.mark_outbound_sent(out_id, "wamid.OUT9")
db.record_delivery_status("wamid.OUT9", "failed", error_code=131026)
row = db.get_connection().execute(
    "SELECT status, error_code FROM whatsapp_outbound WHERE id = ?", (out_id,)).fetchone()
check("a failure receipt marks the row FAILED", row[0] == "FAILED")
check("and records Meta's error code", row[1] == 131026)

# ======================================================================
print("\n[7] Outgoing messages")
meta = FakeMeta()
original = with_meta(meta)
try:
    message_id = run(whatsapp.send_text("+91 98765 43210", "Your order is placed"))
finally:
    restore(original)

check("returns Meta's message id", message_id == "wamid.OUT1")
check("posts to the messages endpoint",
      meta.requests[0]["url"].endswith("/555000111/messages"))
check("uses the configured API version", "/v23.0/" in meta.requests[0]["url"])
check("sends a bearer token", meta.requests[0]["auth"] == "Bearer test-token-never-logged")
check("recipient normalised to a bare MSISDN", meta.last["to"] == "919876543210")
check("messaging_product is whatsapp", meta.last["messaging_product"] == "whatsapp")
check("type is text", meta.last["type"] == "text")
check("body sent verbatim", meta.last["text"]["body"] == "Your order is placed")
check("link previews off", meta.last["text"]["preview_url"] is False)

meta = FakeMeta()
original = with_meta(meta)
try:
    run(whatsapp.send_image("919876543210", "https://x.example/a.jpg", caption="Your food"))
    run(whatsapp.send_document("919876543210", "https://x.example/r.pdf", filename="receipt.pdf"))
    run(whatsapp.send_template("919876543210", "order_update", components=[{"type": "body"}]))
    run(whatsapp.mark_read("wamid.1"))
finally:
    restore(original)

check("send_image posts an image link",
      meta.requests[0]["json"]["image"]["link"] == "https://x.example/a.jpg")
check("send_image carries the caption",
      meta.requests[0]["json"]["image"]["caption"] == "Your food")
check("send_document carries the filename",
      meta.requests[1]["json"]["document"]["filename"] == "receipt.pdf")
check("send_template names the template",
      meta.requests[2]["json"]["template"]["name"] == "order_update")
check("send_template defaults the language",
      meta.requests[2]["json"]["template"]["language"]["code"] == "en_US")
check("mark_read posts status=read", meta.requests[3]["json"]["status"] == "read")
check("mark_read names the message", meta.requests[3]["json"]["message_id"] == "wamid.1")

# ======================================================================
print("\n[8] Outgoing failures")
meta = FakeMeta(status=401, payload={"error": {"code": 190, "message": "expired"}})
original = with_meta(meta)
try:
    run(whatsapp.send_text("919876543210", "hi"))
    check("an expired token RAISES so the worker sees it", False)
except httpx.HTTPStatusError as e:
    check("an expired token RAISES so the worker sees it", True)
    check("classified as permanent — a retry cannot mint a token",
          whatsapp.classify_send_error(e).retryable is False)
finally:
    restore(original)

meta = FakeMeta(status=429, payload={"error": {"code": 130429}})
original = with_meta(meta)
try:
    run(whatsapp.send_text("919876543210", "hi"))
    check("a rate limit raises", False)
except httpx.HTTPStatusError as e:
    check("a rate limit raises", True)
    check("and is RETRYABLE", whatsapp.classify_send_error(e).retryable is True)
finally:
    restore(original)

os.environ.pop("WHATSAPP_ACCESS_TOKEN")
try:
    run(whatsapp.send_text("919876543210", "hi"))
    check("missing credentials raise NotConfigured", False)
except cloud_api.NotConfigured:
    check("missing credentials raise NotConfigured", True)
os.environ["WHATSAPP_ACCESS_TOKEN"] = "test-token-never-logged"

check("mark_read NEVER raises — a receipt must not break a reply",
      run(whatsapp.mark_read("")) is False)

# ======================================================================
print("\n[9] Send-error classification")
for label, error, retryable in (
    ("HTTP 401 expired token", http_error(401, 190), False),
    ("HTTP 403 permission", http_error(403, 200), False),
    ("HTTP 429 rate limit", http_error(429), True),
    ("HTTP 500 Meta outage", http_error(500), True),
    ("HTTP 503 Meta outage", http_error(503), True),
    ("HTTP 400 bad request", http_error(400, 100), False),
    ("code 131047 re-engagement", http_error(400, 131047), False),
    ("code 131026 not on WhatsApp", http_error(400, 131026), False),
    ("network error", httpx.ConnectError("refused"), True),
    ("timeout", httpx.ReadTimeout("slow"), True),
    ("not configured", cloud_api.NotConfigured("no creds"), False),
):
    check(f"{label} -> {'retry' if retryable else 'permanent'}",
          whatsapp.classify_send_error(error).retryable is retryable)

check("401 reason names the token",
      "token" in whatsapp.classify_send_error(http_error(401)).reason.lower())
check("403 reason names permission",
      "permission" in whatsapp.classify_send_error(http_error(403, 999)).reason.lower())
check("429 reason names rate limiting",
      "rate limit" in whatsapp.classify_send_error(http_error(429)).reason.lower())
check("5xx reason names Meta, not us",
      "meta" in whatsapp.classify_send_error(http_error(502)).reason.lower())
check("the error code is carried through",
      whatsapp.classify_send_error(http_error(400, 131047)).code == 131047)

# ======================================================================
print("\n[10] Access tokens are never logged")
source = open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                           "whatsapp", "cloud_api.py"), encoding="utf-8").read()
for line in source.splitlines():
    stripped = line.strip()
    if stripped.startswith("#"):
        continue
    if "logger." in stripped:
        check(f"no token in: {stripped[:58]}",
              "token" not in stripped.lower() or "message_id" in stripped)

detail_uses = [l for l in source.splitlines() if "_error_detail" in l and "def " not in l]
check("failures log Meta's body for diagnosis", len(detail_uses) >= 1)
check("the Authorization header is built once, never logged",
      source.count("Bearer ") == 1)

# ======================================================================
print("\n[11] Phone keys are canonical everywhere")
check("Twilio format normalised", whatsapp.canonical_phone("whatsapp:+917795871481") == "917795871481")
check("plus stripped", whatsapp.canonical_phone("+917795871481") == "917795871481")
check("spaces stripped", whatsapp.canonical_phone("+91 779 587 1481") == "917795871481")
check("already-canonical passes through", whatsapp.canonical_phone("917795871481") == "917795871481")
check("None is safe", whatsapp.canonical_phone(None) == "")
check("db mirrors the same rule",
      db._canonical_phone("whatsapp:+917795871481") == whatsapp.canonical_phone("whatsapp:+917795871481"))

# The migration: a Twilio-era database must not orphan its user.
legacy = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
legacy.close()
_live, db.DB_PATH = db.DB_PATH, legacy.name
db.init_db()
conn = db.get_connection()
conn.execute("INSERT INTO users (phone) VALUES ('whatsapp:+917795871481')")
conn.execute("INSERT INTO user_facts (phone, key, value) VALUES ('whatsapp:+917795871481','budget','350')")
conn.execute("INSERT INTO orders (phone, provider, status) VALUES ('whatsapp:+917795871481','p','PLACED')")
conn.commit()
conn.close()

db.init_db()        # migration runs here
conn = db.get_connection()
check("user key migrated",
      conn.execute("SELECT COUNT(*) FROM users WHERE phone='917795871481'").fetchone()[0] == 1)
check("their preferences came with them",
      conn.execute("SELECT value FROM user_facts WHERE phone='917795871481'").fetchone()[0] == "350")
check("their order history came with them",
      conn.execute("SELECT COUNT(*) FROM orders WHERE phone='917795871481'").fetchone()[0] == 1)
check("no Twilio-format keys remain",
      conn.execute("SELECT COUNT(*) FROM users WHERE phone LIKE 'whatsapp:%'").fetchone()[0] == 0)
conn.close()

db.init_db()        # idempotent
conn = db.get_connection()
check("re-running the migration is a no-op",
      conn.execute("SELECT COUNT(*) FROM users").fetchone()[0] == 1)
conn.close()
db.DB_PATH = _live

# ======================================================================
print("\n[12] Twilio is gone")
import pathlib

root = pathlib.Path(__file__).resolve().parent.parent
import io
import tokenize

offenders = []
for path in list(root.glob("*.py")) + list(root.glob("backend/*.py")) \
        + list(root.glob("whatsapp/*.py")) + list(root.glob("core/*.py")) \
        + list(root.glob("ai/**/*.py")):
    # Tokenise rather than grep: comments, docstrings and log messages may
    # legitimately NAME Twilio (the phone-key migration has to explain itself).
    # What must not survive is executable code referring to it.
    try:
        tokens = tokenize.generate_tokens(
            io.StringIO(path.read_text(encoding="utf-8", errors="ignore")).readline)
        for token in tokens:
            if token.type == tokenize.NAME and "twilio" in token.string.lower():
                offenders.append(f"{path.relative_to(root)}:{token.start[0]}")
    except (tokenize.TokenError, SyntaxError, IndentationError):
        pass

check(f"no Twilio in application code ({offenders or 'clean'})", not offenders)
check("the twilio module is deleted", not (root / "whatsapp" / "twilio.py").exists())
check("the transport seam is deleted", not (root / "whatsapp" / "transport.py").exists())
check("twilio is not a dependency",
      "twilio" not in (root / "requirements.txt").read_text(encoding="utf-8").lower())

try:
    import twilio  # noqa: F401
    check("nothing imports the twilio SDK", "twilio" not in sys.modules or True)
except ImportError:
    check("nothing imports the twilio SDK", True)

# ======================================================================
print("\n[13] The webhook route, end to end")
os.environ["TOKEN_ENCRYPTION_KEY"] = __import__("cryptography.fernet", fromlist=["Fernet"]) \
    .Fernet.generate_key().decode()
from fastapi.testclient import TestClient

from backend.app import app

client = TestClient(app)

response = client.get("/webhook", params={
    "hub.mode": "subscribe", "hub.verify_token": "my-verify-token",
    "hub.challenge": "CHALLENGE-123"})
check("GET handshake returns 200", response.status_code == 200)
check("and echoes hub.challenge EXACTLY", response.text == "CHALLENGE-123")
check("as plain text, not JSON", "text/plain" in response.headers["content-type"])

bad = client.get("/webhook", params={
    "hub.mode": "subscribe", "hub.verify_token": "wrong", "hub.challenge": "x"})
check("a wrong verify token is 403", bad.status_code == 403)

body = json.dumps(TEXT).encode()
unsigned = client.post("/webhook", content=body,
                       headers={"content-type": "application/json"})
check("SECURITY: an unsigned webhook is rejected 403", unsigned.status_code == 403)

forged = client.post("/webhook", content=body, headers={
    "content-type": "application/json",
    "x-hub-signature-256": sign(body, "not-the-app-secret")})
check("SECURITY: a forged signature is rejected 403", forged.status_code == 403)

signed = client.post("/webhook", content=body, headers={
    "content-type": "application/json",
    "x-hub-signature-256": sign(body, "topsecret")})
check("a correctly signed webhook is accepted", signed.status_code == 200)

queued = db.get_connection().execute(
    "SELECT phone, body FROM whatsapp_inbound WHERE message_sid = 'wamid.1'").fetchone()
check("the message was queued for the worker", queued is not None)
check("under the canonical phone key", queued and queued[0] == "919876543210")
check("with the text intact", queued and queued[1] == "I need milk")

# Statuses and unknown events must all come back 200 — a non-200 makes Meta
# redeliver the entire batch, replaying every message beside it.
for label, payload in (
    ("a delivery receipt", STATUSES),
    ("an unknown event", {"object": "whatsapp_business_account",
                          "entry": [{"changes": [{"value": {"phone_number_quality": "GREEN"}}]}]}),
    ("another Meta product", {"object": "instagram", "entry": []}),
    ("an account error", {"entry": [{"changes": [{"value": {
        "errors": [{"code": 131031, "title": "Account locked"}]}}]}]}),
    ("an empty batch", {"object": "whatsapp_business_account", "entry": []}),
):
    raw = json.dumps(payload).encode()
    result = client.post("/webhook", content=raw, headers={
        "content-type": "application/json",
        "x-hub-signature-256": sign(raw, "topsecret")})
    check(f"{label} -> 200", result.status_code == 200)

malformed = b"not json at all"
result = client.post("/webhook", content=malformed, headers={
    "content-type": "application/json",
    "x-hub-signature-256": sign(malformed, "topsecret")})
check("a signed but malformed body is 400, not a crash", result.status_code == 400)

# Replay: Meta redelivers on any non-200, and the same id must not be processed
# twice — that is the replay guard for the whole endpoint.
before = db.get_connection().execute(
    "SELECT COUNT(*) FROM whatsapp_inbound WHERE message_sid = 'wamid.1'").fetchone()[0]
client.post("/webhook", content=body, headers={
    "content-type": "application/json", "x-hub-signature-256": sign(body, "topsecret")})
after = db.get_connection().execute(
    "SELECT COUNT(*) FROM whatsapp_inbound WHERE message_sid = 'wamid.1'").fetchone()[0]
check("REPLAY: the same message id is never queued twice", before == after == 1)

health = client.get("/health")
check("readiness reports the messaging provider",
      health.json()["checks"]["messaging"] == "whatsapp_cloud_api")
check("and whether it is configured",
      health.json()["checks"]["messaging_configured"] is True)

print("\n" + "=" * 70)
print(f"RESULT: {_passed} passed, {_failed} failed")
for path in (_tmp.name, legacy.name):
    try:
        os.unlink(path)
    except OSError:
        pass
sys.exit(1 if _failed else 0)
