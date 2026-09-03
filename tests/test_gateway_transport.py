"""
The messaging layer: whatsapp/gateway.py.

Replaces the Cloud API and neonize suites. The transport is now an HTTP hop to
the Baileys gateway, so what needs pinning changed:

  * no signature to verify   -> a shared secret that FAILS CLOSED
  * no platform error codes  -> gateway HTTP status classification
  * no Graph API version     -> a gateway URL

What did NOT change, and is asserted here, is the contract every layer above
depends on: one canonical phone format, `send_text(phone, body)` returning a
message id, and `classify_send_error` deciding retry-or-fail exactly as
`whatsapp_worker._send_with_retry` reads it.
"""
import asyncio
import io
import logging
import os
import sys
import tokenize
from pathlib import Path
from unittest.mock import AsyncMock, patch

sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

GATEWAY_SECRET = "test-gateway-secret-never-logged"
os.environ["WHATSAPP_GATEWAY_SECRET"] = GATEWAY_SECRET
os.environ["WHATSAPP_GATEWAY_URL"] = "http://localhost:8100"
os.environ["MCP_USE_ANONYMIZED_TELEMETRY"] = "false"

import httpx

import whatsapp
from whatsapp import gateway
from core.logger import logger as app_logger

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


class Response:
    """Stand-in for an httpx response."""

    def __init__(self, status=200, payload=None, text=""):
        self.status_code = status
        self._payload = payload
        self.text = text or ""

    def json(self):
        if self._payload is None:
            raise ValueError("no json")
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise httpx.HTTPStatusError("error", request=None, response=self)


def with_post(response):
    """Patch the outbound HTTP call. Returns (calls, patcher)."""
    calls = []

    async def _post(url, json=None, headers=None):
        calls.append({"url": url, "json": json, "headers": headers or {}})
        if isinstance(response, Exception):
            raise response
        return response

    client = AsyncMock()
    client.__aenter__.return_value = client
    client.__aexit__.return_value = False
    client.post = _post
    return calls, patch.object(httpx, "AsyncClient", return_value=client)


print("=" * 70)
print("MESSAGING LAYER — Baileys WhatsApp gateway transport")
print("=" * 70)

# ======================================================================
print("\n[1] Phone identity is unchanged — bare digits, one key per human")
check("a bare MSISDN passes through",
      whatsapp.canonical_phone("917795871481") == "917795871481")
check("a plus prefix is stripped",
      whatsapp.canonical_phone("+917795871481") == "917795871481")
check("spaces and dashes are stripped",
      whatsapp.canonical_phone("+91 77958-71481") == "917795871481")
check("a legacy provider prefix is stripped",
      whatsapp.canonical_phone("whatsapp:+917795871481") == "917795871481")
check("a JID suffix is stripped, never stored",
      whatsapp.canonical_phone("917795871481@s.whatsapp.net") == "917795871481")
check("a device suffix is stripped",
      whatsapp.canonical_phone("917795871481:12@s.whatsapp.net") == "917795871481")
check("None is empty, not the string 'None'", whatsapp.canonical_phone(None) == "")
check("an empty value stays empty", whatsapp.canonical_phone("") == "")

# ======================================================================
print("\n[2] Configuration FAILS CLOSED")
check("a secret present means configured", gateway.is_configured())
_saved = os.environ.pop("WHATSAPP_GATEWAY_SECRET")
check("no secret means NOT configured", not gateway.is_configured())
raised = None
try:
    run(gateway.send_text("917795871481", "hi"))
except Exception as e:
    raised = e
check("and sending raises rather than posting unauthenticated",
      isinstance(raised, gateway.NotConfigured))
check("classified permanent, so the worker fails it instead of retrying forever",
      not gateway.classify_send_error(raised).retryable)
os.environ["WHATSAPP_GATEWAY_SECRET"] = _saved
check("the URL has a working default", gateway.gateway_url().startswith("http"))

# ======================================================================
print("\n[3] Outgoing text — the worker's call signature is unchanged")
calls, patcher = with_post(Response(200, {"status": "sent", "message_id": "WAMSG-1",
                                          "phone": "917795871481"}))
with patcher:
    message_id = run(gateway.send_text("+91 77958-71481", "Here are three options"))
check("the WhatsApp message id comes back", message_id == "WAMSG-1")
check("it posts to the configured gateway's /send",
      calls[0]["url"] == "http://localhost:8100/send")
check("the recipient is canonicalised before sending",
      calls[0]["json"]["phone"] == "917795871481")
check("the body is sent verbatim",
      calls[0]["json"]["text"] == "Here are three options")
check("the payload is exactly {phone, text} — no Baileys concept crosses over",
      set(calls[0]["json"]) == {"phone", "text"})
check("the secret travels in a header, never in the body",
      calls[0]["headers"]["X-Gateway-Secret"] == GATEWAY_SECRET
      and GATEWAY_SECRET not in str(calls[0]["json"]))

calls, patcher = with_post(Response(200, {"status": "sent"}))
with patcher:
    check("a send with no id returned is not an error",
          run(gateway.send_text("917795871481", "hi")) == "")

# ======================================================================
print("\n[4] HTTP error classification drives retry-or-fail")
cases = [
    (401, False, "our secret is wrong — no retry can fix it"),
    (400, False, "the gateway refused the message"),
    (404, False, "a client error"),
    (503, True, "WhatsApp not connected — transient, the queue rides it out"),
    (500, True, "gateway error"),
    (502, True, "gateway error"),
]
for status, retryable, why in cases:
    error = httpx.HTTPStatusError("x", request=None, response=Response(status))
    verdict = gateway.classify_send_error(error)
    check(f"HTTP {status} -> {'retry' if retryable else 'fail'} ({why})",
          verdict.retryable is retryable and verdict.code == status)

check("a timeout is retryable",
      gateway.classify_send_error(httpx.TimeoutException("slow")).retryable)
check("the gateway being down is retryable — the durable queue exists for this",
      gateway.classify_send_error(httpx.ConnectError("refused")).retryable)
check("an unknown exception is retryable rather than dropping a message",
      gateway.classify_send_error(RuntimeError("?")).retryable)
check("a 401 names the variable to fix",
      "WHATSAPP_GATEWAY_SECRET" in gateway.classify_send_error(
          httpx.HTTPStatusError("x", request=None, response=Response(401))).reason)
check("the verdict is the shape the worker reads",
      gateway.classify_send_error(RuntimeError("?"))._fields
      == ("retryable", "code", "reason"))

# ======================================================================
print("\n[5] A disconnected gateway never pretends to have sent")
calls, patcher = with_post(Response(503, text='{"error":"whatsapp_not_connected"}'))
failed = None
with patcher:
    try:
        run(gateway.send_text("917795871481", "hi"))
    except Exception as e:
        failed = e
check("a 503 raises rather than returning a fake id",
      isinstance(failed, httpx.HTTPStatusError))
check("and the worker will retry it, so the reply is not lost",
      gateway.classify_send_error(failed).retryable)

calls, patcher = with_post(httpx.ConnectError("connection refused"))
failed = None
with patcher:
    try:
        run(gateway.send_text("917795871481", "hi"))
    except Exception as e:
        failed = e
check("an unreachable gateway raises", failed is not None)
check("and is retryable, so a gateway restart costs nobody their message",
      gateway.classify_send_error(failed).retryable)

# ======================================================================
print("\n[6] Secrets and message bodies stay out of the logs")
buffer = io.StringIO()
handler = logging.StreamHandler(buffer)
app_logger.addHandler(handler)
calls, patcher = with_post(Response(200, {"message_id": "WAMSG-2"}))
with patcher:
    run(gateway.send_text("917795871481", "a private message about dinner"))
calls, patcher = with_post(Response(401, text='{"error":"unauthorized"}'))
with patcher:
    try:
        run(gateway.send_text("917795871481", "hi"))
    except Exception:
        pass
app_logger.removeHandler(handler)
logged = buffer.getvalue()

check("the shared secret is never logged", GATEWAY_SECRET not in logged)
check("the message body is not logged",
      "a private message about dinner" not in logged)
check("but the recipient and length are, for debugging",
      "917795871481" in logged and "chars" in logged)
check("a rejection is logged loudly enough to act on", "401" in logged)

# ======================================================================
print("\n[7] Capabilities the gateway does not expose fail LOUDLY")
for name, call in (
    ("send_image", lambda: gateway.send_image("91", "http://example.test/y.jpg")),
    ("send_document", lambda: gateway.send_document("91", "http://example.test/y.pdf")),
    ("send_template", lambda: gateway.send_template("91", "hello")),
):
    raised = None
    try:
        run(call())
    except NotImplementedError as e:
        raised = e
    check(f"{name} raises rather than silently doing nothing", raised is not None)
check("mark_read is a no-op that never raises",
      run(gateway.mark_read("WAMSG-1")) is False)

# ======================================================================
print("\n[8] The seam holds — the backend never learns about Baileys")
root = Path(__file__).resolve().parent.parent
offenders = []
for path in list(root.glob("ai/**/*.py")) + list(root.glob("backend/*.py")) \
        + list(root.glob("core/*.py")):
    with open(path, "rb") as handle:
        for token in tokenize.tokenize(handle.readline):
            if token.type == tokenize.NAME and token.string.lower() == "baileys":
                offenders.append(f"{path.name}:{token.start[0]}")
check(f"no module above whatsapp/ names the transport ({offenders or 'clean'})",
      not offenders)

python_source = "\n".join(
    path.read_text(encoding="utf-8")
    for path in list(root.glob("whatsapp/*.py")) + list(root.glob("backend/*.py"))
    + list(root.glob("ai/**/*.py")) + list(root.glob("core/*.py"))
)
check("no Python file talks to WhatsApp directly",
      "web.whatsapp.com" not in python_source)
check("no Python file talks to a platform Graph API",
      "graph.facebook.com" not in python_source)
check("no Python file imports a WhatsApp protocol library",
      "import neonize" not in python_source and "from neonize" not in python_source)
check("the old transports are deleted",
      not (root / "whatsapp" / "cloud_api.py").exists()
      and not (root / "whatsapp" / "local_client.py").exists())
check("the worker still calls the seam, not a transport module",
      "from whatsapp import classify_send_error, send_text"
      in (root / "backend" / "whatsapp_worker.py").read_text(encoding="utf-8"))

# ======================================================================
print("\n[9] Obsolete configuration is gone")
obsolete = ("WHATSAPP_ACCESS_TOKEN", "WHATSAPP_PHONE_NUMBER_ID",
            "WHATSAPP_APP_SECRET", "WHATSAPP_VERIFY_TOKEN", "META_API_VERSION",
            "WHATSAPP_APP_ID", "WHATSAPP_PROVIDER", "TWILIO_ACCOUNT_SID")
for name in obsolete:
    check(f"{name} is no longer read by any module", name not in python_source)

env_example = (root / ".env.example").read_text(encoding="utf-8")
for name in obsolete:
    check(f"{name} is out of .env.example", name not in env_example)
check("WHATSAPP_GATEWAY_SECRET is documented",
      "WHATSAPP_GATEWAY_SECRET" in env_example)
check("WHATSAPP_GATEWAY_URL is documented", "WHATSAPP_GATEWAY_URL" in env_example)

# ======================================================================
print("\n[10] Session credentials can never be committed")
gitignore = (root / ".gitignore").read_text(encoding="utf-8")
check("the gateway auth directory is gitignored", "auth/" in gitignore)
check("the gateway .env is gitignored",
      "whatsapp-gateway/.env" in gitignore or ".env" in gitignore)
check("node_modules is gitignored", "node_modules" in gitignore)

print("\n" + "=" * 70)
print(f"RESULT: {_passed} passed, {_failed} failed")
print("=" * 70)
sys.exit(1 if _failed else 0)
