"""
WhatsApp Cloud API transport: signature verification (fail-closed), inbound
parsing, and send-error classification.

No network: the send path is exercised through classify_send_error with
synthetic httpx responses.
"""
import hashlib
import hmac
import json
import os
import sys

sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import httpx

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


def sign(body: bytes, secret: str) -> str:
    return "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


def http_error(status, code=None):
    payload = {"error": {"code": code}} if code is not None else {}
    request = httpx.Request("POST", "https://graph.facebook.com/v21.0/1/messages")
    response = httpx.Response(status, json=payload, request=request)
    return httpx.HTTPStatusError("boom", request=request, response=response)


print("\n[1] Signature verification fails CLOSED")
body = json.dumps({"hello": "world"}).encode()
os.environ.pop("WHATSAPP_APP_SECRET", None)
check("no app secret -> rejected", cloud_api.verify_signature(body, sign(body, "s")) is False)

os.environ["WHATSAPP_APP_SECRET"] = "topsecret"
check("valid signature accepted", cloud_api.verify_signature(body, sign(body, "topsecret")))
check("wrong secret rejected", not cloud_api.verify_signature(body, sign(body, "other")))
check("tampered body rejected", not cloud_api.verify_signature(b'{"hello":"evil"}', sign(body, "topsecret")))
check("missing header rejected", not cloud_api.verify_signature(body, ""))
check("malformed header rejected", not cloud_api.verify_signature(body, "md5=abc"))

print("\n[2] Inbound parsing")
text_payload = {
    "entry": [{"changes": [{"value": {"messages": [
        {"id": "wamid.1", "from": "919876543210", "type": "text",
         "text": {"body": "I'm hungry"}}
    ]}}]}]
}
parsed = cloud_api.parse_inbound(text_payload)
check("one text message parsed", len(parsed) == 1)
check("body extracted", parsed[0]["body"] == "I'm hungry")
check("phone extracted", parsed[0]["phone"] == "919876543210")
check("message id extracted (dedup key)", parsed[0]["message_id"] == "wamid.1")
check("text is not flagged unsupported", parsed[0]["unsupported"] is False)

status_payload = {"entry": [{"changes": [{"value": {"statuses": [
    {"id": "wamid.1", "status": "delivered"}
]}}]}]}
check("delivery status callback ignored", cloud_api.parse_inbound(status_payload) == [])

media_payload = {"entry": [{"changes": [{"value": {"messages": [
    {"id": "wamid.2", "from": "9199", "type": "image", "image": {"id": "x"}}
]}}]}]}
media = cloud_api.parse_inbound(media_payload)
check("media flagged unsupported (worker replies politely)", media[0]["unsupported"] is True)

batched = {"entry": [{"changes": [{"value": {"messages": [
    {"id": "a", "from": "1", "type": "text", "text": {"body": "one"}},
    {"id": "b", "from": "2", "type": "text", "text": {"body": "two"}},
]}}]}]}
check("batched messages all parsed", len(cloud_api.parse_inbound(batched)) == 2)
check("empty payload is safe", cloud_api.parse_inbound({}) == [])

print("\n[3] Send-error classification")
check("missing config -> permanent", cloud_api.classify_send_error(RuntimeError("nope")).retryable is False)
check("131047 re-engagement -> permanent", cloud_api.classify_send_error(http_error(400, 131047)).retryable is False)
check("131026 undeliverable -> permanent", cloud_api.classify_send_error(http_error(400, 131026)).retryable is False)
check("190 expired token -> permanent", cloud_api.classify_send_error(http_error(401, 190)).retryable is False)
check("401 auth -> permanent", cloud_api.classify_send_error(http_error(401)).retryable is False)
check("400 client error -> permanent", cloud_api.classify_send_error(http_error(400)).retryable is False)
check("429 rate limit -> retryable", cloud_api.classify_send_error(http_error(429)).retryable is True)
check("500 -> retryable", cloud_api.classify_send_error(http_error(500)).retryable is True)
check("503 -> retryable", cloud_api.classify_send_error(http_error(503)).retryable is True)
check("network error -> retryable", cloud_api.classify_send_error(httpx.ConnectError("down")).retryable is True)
check("timeout -> retryable", cloud_api.classify_send_error(httpx.ReadTimeout("slow")).retryable is True)
check("error code recorded for the outbound row",
      cloud_api.classify_send_error(http_error(400, 131047)).code == 131047)

print("\n[4] Recipient normalisation")
check("strips whatsapp: prefix and +", cloud_api._to_msisdn("whatsapp:+919876543210") == "919876543210")
check("leaves bare number alone", cloud_api._to_msisdn("919876543210") == "919876543210")

print("\n" + "=" * 70)
print(f"RESULT: {_passed} passed, {_failed} failed")
sys.exit(1 if _failed else 0)
