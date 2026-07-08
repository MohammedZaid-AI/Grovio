"""
Tests for the Swiggy checkout payment-selection flow and the raw-error-leak fix.

The live Swiggy MCP is not reachable from CI/sandbox, so the MCP/network
boundary (SwiggyService.build_cart / get_payment_options / checkout) is mocked.
Everything above it — the conversation state machine, the payment-option
formatting, the error parsing/sanitization — is exercised for real.

A separate, un-mocked live script (scripts/live_swiggy_checkout.py) drives the
real MCP end-to-end in an environment that has network + Swiggy auth.
"""
import os
import sys
import json
import asyncio

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

# Dummy creds so core.llm.LLM() constructs at import (no network call happens).
os.environ.setdefault("OPENAI_API_KEY", "test-dummy-key")
os.environ.setdefault("OPENAI_BASE_URL", "https://example.invalid/v1")
os.environ.setdefault("LLM_MODEL", "test-model")

import db
db.DB_PATH = "database/test_swiggy_payment.db"
if os.path.exists(db.DB_PATH):
    os.remove(db.DB_PATH)
db.init_db()

from unittest.mock import patch
from ai.services.swiggy_service import SwiggyService
import ai.services.swiggy_service as swiggy_service_mod

PASS = 0
FAIL = 0


def check(label, cond):
    global PASS, FAIL
    if cond:
        PASS += 1
        print("  [PASS] " + label)
    else:
        FAIL += 1
        print("  [FAIL] " + label)


# Duck-typed stand-ins for MCP CallToolResult objects.
class FakeContent:
    def __init__(self, text):
        self.text = text


class FakeResult:
    def __init__(self, structuredContent=None, text=None, isError=False):
        self.structuredContent = structuredContent
        self.content = [FakeContent(text)] if text is not None else None
        self.isError = isError


print("=" * 78)
print("UNIT: _parse_payment_options (defensive schema normalization)")
print("=" * 78)

svc = SwiggyService()

# structuredContent style, three different label field names
r = FakeResult(structuredContent={"paymentOptions": [
    {"id": "UPI", "displayName": "UPI"},
    {"id": "CARD", "name": "Credit/Debit Card"},
    {"id": "COD", "label": "Cash on Delivery"},
]})
parsed = svc._parse_payment_options(r)
check("structuredContent parsed into 3 options",
      parsed["success"] and len(parsed["options"]) == 3)
check("id + label extracted correctly across field-name variants",
      parsed["options"][0] == {"id": "UPI", "label": "UPI"} and
      parsed["options"][1] == {"id": "CARD", "label": "Credit/Debit Card"} and
      parsed["options"][2] == {"id": "COD", "label": "Cash on Delivery"})

# JSON text fallback with data wrapper and "options"/"code"/"title"
r2 = FakeResult(text=json.dumps({"data": {"options": [{"code": "UPI", "title": "UPI"}]}}))
parsed2 = svc._parse_payment_options(r2)
check("JSON text fallback (data.options / code / title) parsed",
      parsed2["success"] and parsed2["options"] == [{"id": "UPI", "label": "UPI"}])

# empty/unknown -> friendly failure, not a crash
r3 = FakeResult(structuredContent={"somethingElse": True})
parsed3 = svc._parse_payment_options(r3)
check("empty/unknown payment options -> friendly failure (no crash)",
      parsed3["success"] is False and "payment method" in parsed3["message"].lower())

print()
print("=" * 78)
print("UNIT: _parse_error never leaks the raw Swiggy error / Report ID")
print("=" * 78)

async def _fake_recovery(error):
    return {"action": "retry"}

# checkout_recovery.execute calls the LLM; stub it out.
swiggy_service_mod.checkout_recovery.execute = _fake_recovery

raw_error = ("Checkout failed. No payment method selected. Call get_payment_options ... "
             "Report ID: ERR-MRBXZQHQ-99f3a2b1c4d5")
err_result = FakeResult(text=raw_error, isError=True)
res = asyncio.run(svc._parse_error(err_result))

check("user-facing message does NOT contain the raw 'Report ID'",
      "Report ID" not in res["message"] and "ERR-MRBXZQHQ" not in res["message"])
check("payment-method error mapped to NO_PAYMENT_METHOD code",
      res["code"] == "NO_PAYMENT_METHOD")
check("user-facing message is the clean friendly one",
      res["message"] == SwiggyService._FRIENDLY_ERROR_MESSAGES["NO_PAYMENT_METHOD"])
check("raw error still retained separately for server-side logs",
      res.get("raw_error") == raw_error)
print(f"    friendly message shown to user: {res['message']!r}")

print()
print("=" * 78)
print("STATE MACHINE: cart confirm -> payment selection -> checkout(paymentMethod)")
print("=" * 78)

from backend.chat import process_message
from ai.shopping.shopping_session import shopping_session

phone = "whatsapp:+911111111111"
# Seed a session already at the 'checkout' stage with one selected item.
shopping_session.start(phone, [{"name": "Milk", "quantity": 2}])
_sess = shopping_session.get(phone)
_sess["selected"] = [{"displayName": "Milk", "spinId": "SPIN1", "quantity": 2, "price": 30}]
_sess["current"] = 1
_sess["stage"] = "checkout"

calls = {"checkout_payment_method": "NOT_CALLED", "checkout_count": 0}


async def fake_build_cart(self, items):
    return {}


async def fake_get_payment_options(self):
    return {"success": True, "options": [
        {"id": "UPI", "label": "UPI"},
        {"id": "CARD", "label": "Card"},
        {"id": "COD", "label": "Cash on Delivery"},
    ]}


async def fake_checkout(self, payment_method=None):
    calls["checkout_payment_method"] = payment_method
    calls["checkout_count"] += 1
    return {"success": True, "order_placed": True, "order_id": "ORD123",
            "status": "CONFIRMED", "total": 60, "message": "ok"}


def _noop_train(*a, **k):
    return None

with patch.object(SwiggyService, "build_cart", fake_build_cart), \
     patch.object(SwiggyService, "get_payment_options", fake_get_payment_options), \
     patch.object(SwiggyService, "checkout", fake_checkout), \
     patch("ai.memory.memory_trainer.memory_trainer.train", _noop_train):

    # Step 1: user confirms cart with YES -> gets payment prompt, checkout NOT called.
    resp1 = asyncio.run(process_message(phone, "yes"))
    check("YES at cart confirm -> payment method prompt shown",
          "Choose Payment Method" in resp1 and "1. UPI" in resp1 and "3. Cash on Delivery" in resp1)
    check("stage advanced to 'payment_selection'",
          shopping_session.get_stage(phone) == "payment_selection")
    check("checkout NOT called yet (waits for explicit payment choice)",
          calls["checkout_payment_method"] == "NOT_CALLED")

    # Step 1b: garbage input -> re-prompt, still no checkout.
    resp_bad = asyncio.run(process_message(phone, "abc"))
    check("non-numeric payment input -> re-prompt (no auto-proceed)",
          "Choose Payment Method" in resp_bad and calls["checkout_payment_method"] == "NOT_CALLED")

    # Step 1c: out-of-range -> re-prompt, still no checkout.
    resp_oor = asyncio.run(process_message(phone, "9"))
    check("out-of-range payment input -> re-prompt with bounds",
          "between 1 and 3" in resp_oor and calls["checkout_payment_method"] == "NOT_CALLED")

    # Step 2: valid selection (2 = Card) -> checkout called WITH paymentMethod='CARD'.
    resp2 = asyncio.run(process_message(phone, "2"))
    check("selecting '2' calls checkout WITH paymentMethod='CARD'",
          calls["checkout_payment_method"] == "CARD" and calls["checkout_count"] == 1)
    check("successful checkout returns order confirmation",
          "Order placed successfully" in resp2 and "ORD123" in resp2)
    check("session ended after successful order",
          not shopping_session.has_session(phone))

print()
print("=" * 78)
print("RESULT: %d passed, %d failed" % (PASS, FAIL))
print("=" * 78)

if os.path.exists(db.DB_PATH):
    os.remove(db.DB_PATH)
    print("Test database cleaned up successfully.")

sys.exit(1 if FAIL else 0)
