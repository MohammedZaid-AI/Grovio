"""
Regression tests for SwiggyService._parse_success (checkout result parsing).

The bug: _parse_success json.loads()'d the human-readable text even though MCP
already returned structuredContent, raising "Expecting value: line 1 column 1"
and (worse) reporting order_placed=False for a CONFIRMED order — which left the
shopping session un-cleared.

These tests pin the fixed behaviour:
  * structuredContent is used directly (UPI + COD)
  * plain-text success is parsed via regex fallback
  * plain-text widget notice / non-JSON never throws and is still order_placed
  * every path yields order_placed=True (checkout only calls this on isError=False)

Run:  python tests/test_swiggy_parser.py
"""
import os
import sys

sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from ai.services.swiggy_service import SwiggyService

svc = SwiggyService()
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


class FakeContent:
    def __init__(self, text):
        self.text = text


class FakeResult:
    def __init__(self, structured=None, text=None, is_error=False):
        self.structuredContent = structured
        self.content = [FakeContent(text)] if text is not None else None
        self.isError = is_error


def test_upi_structured():
    print("\n[1] UPI structuredContent parsed directly (with human-readable text present)")
    structured = {"data": {
        "orderId": "OD-UPI-1", "status": "CONFIRMED", "paymentMethod": "UPI",
        "cartTotal": 240, "bridgeUrl": "https://swiggy/bridge",
        "upiIntentUrl": "upi://pay?pa=x", "paasId": "PAAS-9",
        "transactionId": "TXN-7", "redirectUrl": "https://swiggy/redirect",
        "QR": "qr-blob",
    }, "message": "Order placed"}
    # Human-readable text that is NOT json — the old code crashed on this.
    r = svc._parse_success(FakeResult(structured=structured, text="Your order is confirmed!"))
    check("no exception; order_placed", r.get("order_placed") is True)
    check("success True", r.get("success") is True)
    check("order_id", r.get("order_id") == "OD-UPI-1")
    check("status", r.get("status") == "CONFIRMED")
    check("payment UPI", r.get("payment") == "UPI")
    check("total", r.get("total") == 240)
    check("bridge_url", r.get("bridge_url") == "https://swiggy/bridge")
    check("upi_intent_url", r.get("upi_intent_url") == "upi://pay?pa=x")
    check("paas_id", r.get("paas_id") == "PAAS-9")
    check("transaction_id", r.get("transaction_id") == "TXN-7")
    check("redirect_url", r.get("redirect_url") == "https://swiggy/redirect")
    check("qr", r.get("qr") == "qr-blob")


def test_cod_structured():
    print("\n[2] COD structuredContent")
    structured = {"data": {"orderId": "OD-COD-2", "status": "CONFIRMED", "paymentMethod": "Cash", "cartTotal": 100}}
    r = svc._parse_success(FakeResult(structured=structured))
    check("order_placed", r.get("order_placed") is True)
    check("success True", r.get("success") is True)
    check("order_id", r.get("order_id") == "OD-COD-2")
    check("payment Cash", r.get("payment") == "Cash")
    check("no upi fields", r.get("upi_intent_url") is None and r.get("qr") is None)


def test_top_level_fields():
    print("[2b] structuredContent with fields at top level (no 'data' wrapper)")
    structured = {"orderId": "OD-FLAT", "status": "CONFIRMED", "paymentMethod": "UPI"}
    r = svc._parse_success(FakeResult(structured=structured))
    check("order_id from top level", r.get("order_id") == "OD-FLAT")
    check("success True", r.get("success") is True)


def test_json_text_only():
    print("\n[3a] JSON in text, no structuredContent -> json.loads used")
    text = '{"data": {"orderId": "OD-JSON", "status": "CONFIRMED", "paymentMethod": "UPI", "cartTotal": 55}}'
    r = svc._parse_success(FakeResult(structured=None, text=text))
    check("order_placed", r.get("order_placed") is True)
    check("order_id parsed", r.get("order_id") == "OD-JSON")
    check("success True", r.get("success") is True)


def test_plaintext_success():
    print("[3b] plain-text success -> regex fallback extracts fields")
    text = "Your order is CONFIRMED. Order ID: OD-TXT-3. Total: 320."
    r = svc._parse_success(FakeResult(structured=None, text=text))
    check("order_placed", r.get("order_placed") is True)
    check("order_id via regex", r.get("order_id") == "OD-TXT-3")
    check("status via regex", (r.get("status") or "").upper() == "CONFIRMED")
    check("success True (fields found)", r.get("success") is True)


def test_plaintext_widget_notice():
    print("\n[4] plain-text widget notice -> placed, no fields, no throw")
    text = "Please complete your payment in the Swiggy app to finish checkout."
    r = svc._parse_success(FakeResult(structured=None, text=text))
    check("order_placed True (isError was False)", r.get("order_placed") is True)
    check("success False (nothing concrete parsed)", r.get("success") is False)
    check("status defaults to CONFIRMED", r.get("status") == "CONFIRMED")
    check("no order_id", r.get("order_id") is None)


def test_non_json_content():
    print("\n[5] non-JSON content that starts like junk -> no exception")
    for text in ["Expecting value nonsense", "", "OK!", "<html>widget</html>"]:
        r = svc._parse_success(FakeResult(structured=None, text=text))
        check(f"order_placed for text={text!r}", r.get("order_placed") is True)


def test_structured_plus_junk_text_the_actual_bug():
    print("\n[6] THE BUG: structuredContent present + non-JSON text -> uses structured, no crash")
    structured = {"data": {"orderId": "OD-BUG", "status": "CONFIRMED", "paymentMethod": "Cash"}}
    r = svc._parse_success(FakeResult(structured=structured, text="Expecting value: line 1 column 1"))
    check("used structuredContent", r.get("order_id") == "OD-BUG")
    check("order_placed True", r.get("order_placed") is True)
    check("success True", r.get("success") is True)


def test_none_result():
    print("[7] None result -> soft failure (cannot confirm)")
    r = svc._parse_success(None)
    check("not order_placed", r.get("order_placed") is False)


if __name__ == "__main__":
    test_upi_structured()
    test_cod_structured()
    test_top_level_fields()
    test_json_text_only()
    test_plaintext_success()
    test_plaintext_widget_notice()
    test_non_json_content()
    test_structured_plus_junk_text_the_actual_bug()
    test_none_result()

    print("\n" + "=" * 78)
    print(f"RESULT: {_passed} passed, {_failed} failed")
    print("=" * 78)
    sys.exit(1 if _failed else 0)
