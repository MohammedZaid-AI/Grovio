"""
MVP regression: checkout is Cash on Delivery only.

Asserts the simplified conversation:

    cart summary -> user "yes" -> checkout(paymentMethod="Cash") -> confirmation

The user is NEVER shown a payment menu, get_payment_options is NEVER called, and
the shopping session is ended so the next order starts fresh.

The online-payment machinery is retained (backend.chat.PAYMENT_SELECTION_ENABLED)
and is covered by tests/test_swiggy_payment_flow.py.

Run:  python tests/test_cod_only_checkout.py
"""
import asyncio
import os
import sys
import tempfile
from unittest.mock import patch

sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.setdefault("JWT_SECRET", "x" * 48)

import db
_tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
_tmp.close()
db.DB_PATH = _tmp.name
db.init_db()

import backend.chat as chat_module
from backend.chat import process_message
from ai.services.swiggy_service import SwiggyService
from ai.shopping.shopping_session import shopping_session

_passed = 0
_failed = 0


def check(name, condition):
    global _passed, _failed
    if condition:
        _passed += 1
        print(f"  ✅ {name}")
    else:
        _failed += 1
        print(f"  ❌ {name}")


def _seed_checkout_session(phone, items, selected):
    shopping_session.start(phone, items)
    s = shopping_session.get(phone)
    s["selected"] = selected
    s["current"] = len(items)
    s["stage"] = "checkout"


checkout_calls = []
payment_option_calls = []


async def fake_build_cart(self, items):
    return {}


async def fake_checkout(self, payment_method=None, intent_app=None, generate_upi_qr=False):
    checkout_calls.append({"payment_method": payment_method, "intent_app": intent_app,
                           "generate_upi_qr": generate_upi_qr})
    return {"success": True, "order_placed": True, "order_id": "ORD-COD-1",
            "status": "CONFIRMED", "total": 60, "message": "ok"}


async def fake_get_payment_options(self):
    payment_option_calls.append(True)
    return {"success": True, "options": [{"label": "Google Pay", "paymentMethod": "UPI",
                                          "intentApp": "gpay://", "generateUPIQR": False}]}


def _noop_train(*a, **k):
    return None


def _fake_graph_invoke(state):
    # Stub the LangGraph/LLM fallback so a message with no shopping session never
    # reaches Groq during these tests.
    return {**state, "response": "(no shopping session)"}


def _mocks():
    return (
        patch.object(SwiggyService, "build_cart", fake_build_cart),
        patch.object(SwiggyService, "get_payment_options", fake_get_payment_options),
        patch.object(SwiggyService, "checkout", fake_checkout),
        patch("ai.memory.memory_trainer.memory_trainer.train", _noop_train),
        patch.object(chat_module.graph, "invoke", _fake_graph_invoke),
    )


def run(phone, msg):
    m = _mocks()
    with m[0], m[1], m[2], m[3], m[4]:
        return asyncio.run(process_message(phone, msg))


def test_flag_default():
    print("\n[1] Payment selection is disabled by default (MVP)")
    check("PAYMENT_SELECTION_ENABLED is False", chat_module.PAYMENT_SELECTION_ENABLED is False)
    check("COD_PAYMENT_METHOD is 'Cash'", chat_module.COD_PAYMENT_METHOD == "Cash")


def test_single_item():
    print("\n[2] Single-item order -> instant COD checkout, no payment prompt")
    checkout_calls.clear(); payment_option_calls.clear()
    ph = "whatsapp:+919000000001"
    _seed_checkout_session(ph, [{"name": "Coke", "quantity": 1}],
                           [{"displayName": "Coke", "spinId": "S1", "skuId": "K1", "quantity": 1, "price": 40}])
    r = run(ph, "yes")

    check("checkout called exactly once", len(checkout_calls) == 1)
    check("paymentMethod == 'Cash'", checkout_calls and checkout_calls[0]["payment_method"] == "Cash")
    check("no intentApp / no QR", checkout_calls and checkout_calls[0]["intent_app"] is None
          and checkout_calls[0]["generate_upi_qr"] is False)
    check("get_payment_options NEVER called", len(payment_option_calls) == 0)
    check("confirmation says Order Confirmed", "Order Confirmed" in r)
    check("confirmation mentions Cash on Delivery", "Cash on Delivery" in r)
    check("order id shown", "ORD-COD-1" in r)
    check("no UPI mention", "UPI" not in r)
    check("no payment menu wording", "Choose Payment Method" not in r and "how would you like to pay" not in r.lower())
    check("shopping session ended", not shopping_session.has_session(ph))


def test_multi_item():
    print("\n[3] Multi-item order -> single COD checkout")
    checkout_calls.clear(); payment_option_calls.clear()
    ph = "whatsapp:+919000000002"
    _seed_checkout_session(
        ph,
        [{"name": "Coke", "quantity": 1}, {"name": "Ice cream", "quantity": 2}],
        [{"displayName": "Coke", "spinId": "S1", "skuId": "K1", "quantity": 1, "price": 40},
         {"displayName": "Ice cream", "spinId": "S2", "skuId": "K2", "quantity": 2, "price": 90}],
    )
    r = run(ph, "yes")
    check("checkout called once with Cash", len(checkout_calls) == 1
          and checkout_calls[0]["payment_method"] == "Cash")
    check("no payment options fetched", len(payment_option_calls) == 0)
    check("confirmed", "Order Confirmed" in r)
    check("session ended", not shopping_session.has_session(ph))


def test_no_double_order():
    print("\n[4] Double 'yes' does not place a second order")
    checkout_calls.clear()
    ph = "whatsapp:+919000000003"
    _seed_checkout_session(ph, [{"name": "Coke", "quantity": 1}],
                           [{"displayName": "Coke", "spinId": "S1", "quantity": 1, "price": 40}])
    r1 = run(ph, "yes")
    check("first yes places order", len(checkout_calls) == 1 and "Order Confirmed" in r1)
    # Session is gone; a stray 'yes' must not re-checkout.
    run(ph, "yes")
    check("second yes does not re-checkout", len(checkout_calls) == 1)


def test_declining_at_cart():
    print("\n[5] Non-'yes' at cart does not check out")
    checkout_calls.clear()
    ph = "whatsapp:+919000000004"
    _seed_checkout_session(ph, [{"name": "Coke", "quantity": 1}],
                           [{"displayName": "Coke", "spinId": "S1", "quantity": 1, "price": 40}])
    r = run(ph, "maybe")
    check("no checkout call", len(checkout_calls) == 0)
    check("re-prompted for YES", "YES" in r.upper())
    check("session still active", shopping_session.has_session(ph))
    shopping_session.end(ph)


def test_new_order_starts_fresh():
    print("\n[6] A new order after checkout starts fresh")
    checkout_calls.clear()
    ph = "whatsapp:+919000000005"
    _seed_checkout_session(ph, [{"name": "Coke", "quantity": 1}],
                           [{"displayName": "Coke", "spinId": "S1", "quantity": 1, "price": 40}])
    run(ph, "yes")
    check("session ended after order", not shopping_session.has_session(ph))
    shopping_session.start(ph, [{"name": "Milk", "quantity": 1}])
    check("fresh session has empty selection", shopping_session.selected(ph) == [])
    check("fresh lifecycle is draft", shopping_session.lifecycle(ph) == "draft")
    shopping_session.end(ph)


def test_payment_path_still_available():
    print("\n[7] Online-payment path is retained and re-enablable")
    checkout_calls.clear(); payment_option_calls.clear()
    ph = "whatsapp:+919000000006"
    _seed_checkout_session(ph, [{"name": "Coke", "quantity": 1}],
                           [{"displayName": "Coke", "spinId": "S1", "quantity": 1, "price": 40}])
    chat_module.PAYMENT_SELECTION_ENABLED = True
    try:
        r = run(ph, "yes")
        check("payment menu shown when re-enabled", "Choose Payment Method" in r)
        check("get_payment_options called when re-enabled", len(payment_option_calls) == 1)
        check("checkout deferred until method chosen", len(checkout_calls) == 0)
    finally:
        chat_module.PAYMENT_SELECTION_ENABLED = False
        shopping_session.end(ph)
    check("flag restored to MVP default", chat_module.PAYMENT_SELECTION_ENABLED is False)


if __name__ == "__main__":
    try:
        test_flag_default()
        test_single_item()
        test_multi_item()
        test_no_double_order()
        test_declining_at_cart()
        test_new_order_starts_fresh()
        test_payment_path_still_available()
    finally:
        try:
            os.unlink(_tmp.name)
        except OSError:
            pass

    print("\n" + "=" * 78)
    print(f"RESULT: {_passed} passed, {_failed} failed")
    print("=" * 78)
    sys.exit(1 if _failed else 0)
