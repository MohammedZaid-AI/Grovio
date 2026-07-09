"""
Tests for the Swiggy checkout payment flow:
  - payment-option parsing against Swiggy's REAL schema (platforms / cod / allMethods)
  - the raw-error-leak fix
  - the state machine: options-available path AND the Cash-on-Delivery fallback
    (used when get_payment_options is unavailable for the account).

The live Swiggy MCP is not reachable from CI/sandbox, so the MCP/network
boundary (SwiggyService.get_payment_options / checkout / build_cart) is mocked.
Everything above it runs for real. scripts/live_swiggy_checkout.py drives the
real MCP end-to-end in an environment with network + Swiggy auth.
"""
import os
import sys
import json
import asyncio

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

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


class FakeContent:
    def __init__(self, text):
        self.text = text


class FakeResult:
    def __init__(self, structuredContent=None, text=None, isError=False):
        self.structuredContent = structuredContent
        self.content = [FakeContent(text)] if text is not None else None
        self.isError = isError


print("=" * 78)
print("UNIT: _parse_payment_options against Swiggy's real schema")
print("=" * 78)

svc = SwiggyService()

# Real-shape response: a UPI mobile app + Cash on Delivery.
r = FakeResult(structuredContent={
    "platforms": {"mobile": {"methods": [{"id": "gpay://upi/", "displayName": "Google Pay"}]}},
    "cod": {"displayName": "Cash on Delivery"},
})
parsed = svc._parse_payment_options(r)
check("parses real schema into 2 options (UPI app + Cash)",
      parsed["success"] and len(parsed["options"]) == 2)
upi_opt = parsed["options"][0]
cod_opt = parsed["options"][1]
check("UPI app -> paymentMethod='UPI', intentApp=<app id> (per checkout schema)",
      upi_opt["paymentMethod"] == "UPI" and upi_opt["intentApp"] == "gpay://upi/")
check("Cash -> paymentMethod='Cash', no intentApp",
      cod_opt["paymentMethod"] == "Cash" and cod_opt["intentApp"] is None)

# Cash-only account (UPI not whitelisted): only allMethods / cod present.
r2 = FakeResult(structuredContent={"cod": True})
parsed2 = svc._parse_payment_options(r2)
check("Cash-only response yields a single Cash option",
      parsed2["success"] and len(parsed2["options"]) == 1 and parsed2["options"][0]["paymentMethod"] == "Cash")

# Empty/unknown -> friendly failure (this is the real-world 'isError' fallback trigger).
r3 = FakeResult(structuredContent={})
parsed3 = svc._parse_payment_options(r3)
check("empty response -> friendly failure (triggers COD fallback upstream)",
      parsed3["success"] is False)

print()
print("=" * 78)
print("UNIT: _parse_error never leaks raw Swiggy error / Report ID")
print("=" * 78)

async def _fake_recovery(error):
    return {"action": "retry"}

swiggy_service_mod.checkout_recovery.execute = _fake_recovery

raw_error = "Checkout failed. Report ID: ERR-MRBXZQHQ-99f3a2b1c4d5"
res = asyncio.run(svc._parse_error(FakeResult(text=raw_error, isError=True)))
check("user message does NOT contain raw 'Report ID'/'ERR-'",
      "Report ID" not in res["message"] and "ERR-" not in res["message"])
check("raw error retained separately for logs",
      res.get("raw_error") == raw_error)

print()
print("=" * 78)
print("STATE MACHINE")
print("=" * 78)

from backend.chat import process_message
from ai.shopping.shopping_session import shopping_session


def _seed_checkout_session(phone):
    shopping_session.start(phone, [{"name": "Milk", "quantity": 2}])
    s = shopping_session.get(phone)
    s["selected"] = [{"displayName": "Milk", "spinId": "SPIN1", "quantity": 2, "price": 30}]
    s["current"] = 1
    s["stage"] = "checkout"


checkout_calls = []


async def fake_build_cart(self, items):
    return {}


async def fake_checkout(self, payment_method=None, intent_app=None, generate_upi_qr=False):
    checkout_calls.append({"payment_method": payment_method, "intent_app": intent_app,
                           "generate_upi_qr": generate_upi_qr})
    return {"success": True, "order_placed": True, "order_id": "ORD123",
            "status": "CONFIRMED", "total": 60, "message": "ok"}


def _noop_train(*a, **k):
    return None


# ---- Scenario A: options available -> user chooses a UPI app ----
async def opts_available(self):
    return {"success": True, "options": [
        {"label": "Google Pay", "paymentMethod": "UPI", "intentApp": "gpay://upi/", "generateUPIQR": False},
        {"label": "Cash on Delivery", "paymentMethod": "Cash", "intentApp": None, "generateUPIQR": False},
    ]}

print("\nScenario A: payment options available -> user selects UPI app")
checkout_calls.clear()
phone_a = "whatsapp:+911111111111"
_seed_checkout_session(phone_a)
with patch.object(SwiggyService, "build_cart", fake_build_cart), \
     patch.object(SwiggyService, "get_payment_options", opts_available), \
     patch.object(SwiggyService, "checkout", fake_checkout), \
     patch("ai.memory.memory_trainer.memory_trainer.train", _noop_train):
    r1 = asyncio.run(process_message(phone_a, "yes"))
    check("YES -> payment options prompt shown; checkout NOT called",
          "Choose Payment Method" in r1 and "1. Google Pay" in r1 and len(checkout_calls) == 0)
    check("stage advanced to payment_selection",
          shopping_session.get_stage(phone_a) == "payment_selection")
    r2 = asyncio.run(process_message(phone_a, "1"))
    check("selecting UPI app -> checkout(paymentMethod='UPI', intentApp='gpay://upi/')",
          len(checkout_calls) == 1 and checkout_calls[0]["payment_method"] == "UPI"
          and checkout_calls[0]["intent_app"] == "gpay://upi/")
    check("order confirmation returned", "Order placed successfully" in r2)


# ---- Scenario B: options unavailable -> COD fallback, user confirms ----
async def opts_unavailable(self):
    return {"success": False, "message": "unavailable"}

print("\nScenario B: options unavailable -> Cash on Delivery fallback, user confirms")
checkout_calls.clear()
phone_b = "whatsapp:+912222222222"
_seed_checkout_session(phone_b)
with patch.object(SwiggyService, "build_cart", fake_build_cart), \
     patch.object(SwiggyService, "get_payment_options", opts_unavailable), \
     patch.object(SwiggyService, "checkout", fake_checkout), \
     patch("ai.memory.memory_trainer.memory_trainer.train", _noop_train):
    r1 = asyncio.run(process_message(phone_b, "yes"))
    check("options unavailable -> clear COD notice shown; checkout NOT called yet",
          "Cash on Delivery" in r1 and "Reply YES to confirm" in r1 and len(checkout_calls) == 0)
    check("stage advanced to cod_confirm",
          shopping_session.get_stage(phone_b) == "cod_confirm")
    r2 = asyncio.run(process_message(phone_b, "yes"))
    check("confirming -> checkout(paymentMethod='Cash')",
          len(checkout_calls) == 1 and checkout_calls[0]["payment_method"] == "Cash")
    check("order confirmation returned", "Order placed successfully" in r2)


# ---- Scenario C: COD fallback, user declines ----
print("\nScenario C: Cash on Delivery fallback, user declines")
checkout_calls.clear()
phone_c = "whatsapp:+913333333333"
_seed_checkout_session(phone_c)
with patch.object(SwiggyService, "build_cart", fake_build_cart), \
     patch.object(SwiggyService, "get_payment_options", opts_unavailable), \
     patch.object(SwiggyService, "checkout", fake_checkout), \
     patch("ai.memory.memory_trainer.memory_trainer.train", _noop_train):
    asyncio.run(process_message(phone_c, "yes"))
    r2 = asyncio.run(process_message(phone_c, "no"))
    check("declining -> order cancelled, checkout NOT called, session ended",
          "cancelled" in r2.lower() and len(checkout_calls) == 0 and not shopping_session.has_session(phone_c))


# ---- Scenario D: checkout fails -> surfaced cleanly, no silent retry ----
async def failing_checkout(self, payment_method=None, intent_app=None, generate_upi_qr=False):
    checkout_calls.append({"payment_method": payment_method})
    return {"success": False, "order_placed": False, "code": "UNKNOWN",
            "message": "⚠️ We couldn't complete the checkout right now. Please try again in a few minutes."}

print("\nScenario D: checkout fails -> clean surface, no silent retry")
checkout_calls.clear()
phone_d = "whatsapp:+914444444444"
_seed_checkout_session(phone_d)
with patch.object(SwiggyService, "build_cart", fake_build_cart), \
     patch.object(SwiggyService, "get_payment_options", opts_unavailable), \
     patch.object(SwiggyService, "checkout", failing_checkout), \
     patch("ai.memory.memory_trainer.memory_trainer.train", _noop_train):
    asyncio.run(process_message(phone_d, "yes"))     # -> cod_confirm
    rd = asyncio.run(process_message(phone_d, "yes"))  # -> checkout fails
    check("checkout attempted exactly once (no silent retry)", len(checkout_calls) == 1)
    check("clean friendly error surfaced to user (no raw text)",
          "couldn't complete the checkout" in rd and "ERR-" not in rd)


# ---- Scenario E: cart is rebuilt in the SAME session right before checkout ----
# This is the regression fix: the checkout message runs in a fresh Swiggy MCP
# session, so the cart must be rebuilt there or Swiggy returns
# "Cart not found or session expired".
events = []  # (event_name, id(service_instance))


async def rec_build_cart(self, items):
    events.append(("build_cart", id(self)))
    return {}


async def rec_checkout(self, payment_method=None, intent_app=None, generate_upi_qr=False):
    events.append(("checkout", id(self)))
    return {"success": True, "order_placed": True, "order_id": "ORDX",
            "status": "CONFIRMED", "total": 60, "message": "ok"}


def _assert_persistent_session(msg1_events, all_events, label):
    # Increment 2: the cart is built ONCE (msg 1) and checkout happens in msg 2
    # on the SAME persistent Swiggy session — no rebuild between messages.
    msg2 = all_events[len(msg1_events):]
    ok = (
        len(msg1_events) == 1
        and msg1_events[0][0] == "build_cart"
        and [e[0] for e in msg2] == ["checkout"]              # no rebuild in msg 2
        and msg1_events[0][1] == msg2[0][1]                   # same persistent instance
    )
    check(f"{label}: cart built once, checkout on the SAME persistent session (no rebuild between messages)",
          ok)


print("\nScenario E: COD path — persistent session carries the cart to checkout")
events.clear()
phone_e = "whatsapp:+915555555555"
_seed_checkout_session(phone_e)
with patch.object(SwiggyService, "build_cart", rec_build_cart), \
     patch.object(SwiggyService, "get_payment_options", opts_unavailable), \
     patch.object(SwiggyService, "checkout", rec_checkout), \
     patch("ai.memory.memory_trainer.memory_trainer.train", _noop_train):
    asyncio.run(process_message(phone_e, "yes"))   # msg 1 (checkout stage): build cart once
    msg1_events = list(events)
    asyncio.run(process_message(phone_e, "yes"))   # msg 2 (cod_confirm): checkout, no rebuild
_assert_persistent_session(msg1_events, events, "COD confirm")


print("\nScenario F: UPI selection path — same persistent session, no rebuild")
events.clear()
phone_f = "whatsapp:+916666666666"
_seed_checkout_session(phone_f)
with patch.object(SwiggyService, "build_cart", rec_build_cart), \
     patch.object(SwiggyService, "get_payment_options", opts_available), \
     patch.object(SwiggyService, "checkout", rec_checkout), \
     patch("ai.memory.memory_trainer.memory_trainer.train", _noop_train):
    asyncio.run(process_message(phone_f, "yes"))   # msg 1 (checkout stage): build cart once
    msg1_events = list(events)
    asyncio.run(process_message(phone_f, "1"))     # msg 2 (payment_selection): checkout, no rebuild
_assert_persistent_session(msg1_events, events, "payment selection")


print()
print("=" * 78)
print("RESULT: %d passed, %d failed" % (PASS, FAIL))
print("=" * 78)

if os.path.exists(db.DB_PATH):
    os.remove(db.DB_PATH)
    print("Test database cleaned up successfully.")

sys.exit(1 if FAIL else 0)
