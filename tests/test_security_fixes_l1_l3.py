"""
Regression tests for security fixes L-1, L-2, L-3.

L-1: dashboard.html / login.html are only reachable through the JWT-protected
     /admin routes, never as static files.
L-2: unhandled exceptions in admin routes never leak raw exception text to
     the client — they're logged server-side and a generic message is returned.
L-3: debug artifacts (console dump of full message content, raw OCR text
     persisted to disk) are gated behind DEBUG and off by default.
"""
import io
import os
import sys
import contextlib
from unittest.mock import patch

os.environ["JWT_SECRET"] = "test-secret-key-very-secure-length-32-bytes"
os.environ["DASHBOARD_PASSWORD"] = "test-admin-password"
os.environ.pop("DEBUG", None)  # start with DEBUG unset (production default)

import db
db.DB_PATH = "database/test_security_l1_l3.db"
if os.path.exists(db.DB_PATH):
    os.remove(db.DB_PATH)
db.init_db()

from fastapi.testclient import TestClient
from backend.app import app
import backend.routes as routes

client = TestClient(app, base_url="https://testserver")

print("\n" + "=" * 70)
print("SECURITY FIXES L-1 / L-2 / L-3 REGRESSION TESTS")
print("=" * 70)


def run_tests():
    # -----------------------------------------------------------
    # L-1: static HTML pages no longer bypassable
    # -----------------------------------------------------------
    print("\nTest L-1: Dashboard/login HTML not reachable via /static...")

    resp = client.get("/static/dashboard.html")
    assert resp.status_code == 404, f"Expected 404, got {resp.status_code}"
    print("  - GET /static/dashboard.html -> 404 [OK]")

    resp = client.get("/static/login.html")
    assert resp.status_code == 404, f"Expected 404, got {resp.status_code}"
    print("  - GET /static/login.html -> 404 [OK]")

    # CSS/JS are legitimately public and must still work.
    resp = client.get("/static/css/style.css")
    assert resp.status_code == 200, f"Expected 200, got {resp.status_code}"
    resp = client.get("/static/js/dashboard.js")
    assert resp.status_code == 200, f"Expected 200, got {resp.status_code}"
    print("  - /static/css/style.css and /static/js/dashboard.js still public [OK]")

    # /admin still works correctly.
    resp = client.get("/admin", follow_redirects=False)
    assert resp.status_code == 303, f"Expected 303 (unauthenticated), got {resp.status_code}"
    print("  - GET /admin (unauthenticated) -> 303 redirect to login [OK]")

    client.post("/admin/login", data={"password": "test-admin-password"})
    resp = client.get("/admin", follow_redirects=False)
    assert resp.status_code == 200, f"Expected 200 (authenticated), got {resp.status_code}"
    assert "Grovio" in resp.text or "dashboard" in resp.text.lower()
    print("  - GET /admin (authenticated) -> 200, serves dashboard HTML [OK]")

    # -----------------------------------------------------------
    # L-2: no raw exception text in API responses
    # -----------------------------------------------------------
    print("\nTest L-2: No raw exception text leaked to the client...")

    sensitive_detail = "sqlite3.OperationalError: no such column: totally_internal_schema_field at /secret/path/db.py:1234"

    with patch("db.save_recipe", side_effect=Exception(sensitive_detail)):
        resp = client.post("/admin/recipes", json={
            "dish_name": "Test Dish",
            "ingredients": [{"ingredient_name": "flour", "quantity_per_unit": 1.0, "unit": "kg"}]
        })

    assert resp.status_code == 500, f"Expected 500, got {resp.status_code}"
    body = resp.json()
    assert sensitive_detail not in resp.text, "SECURITY FAILURE: raw exception text leaked to client!"
    assert "totally_internal_schema_field" not in resp.text, "SECURITY FAILURE: internal schema detail leaked!"
    assert body["message"] == routes.GENERIC_ERROR_MESSAGE, f"Expected generic message, got: {body['message']}"
    print(f"  - Triggered DB error: client receives generic message only: {body['message']!r} [OK]")
    print(f"  - Raw exception text ('{sensitive_detail[:40]}...') NOT present anywhere in response [OK]")

    # -----------------------------------------------------------
    # L-3: debug artifacts gated behind DEBUG (default off)
    # -----------------------------------------------------------
    print("\nTest L-3: Debug artifacts gated behind DEBUG flag...")

    from twilio.request_validator import RequestValidator
    os.environ["TWILIO_AUTH_TOKEN"] = "test-twilio-auth-token"
    validator = RequestValidator(os.environ["TWILIO_AUTH_TOKEN"])
    webhook_data = {"Body": "hello", "From": "whatsapp:+1234567890"}
    webhook_url = "http://testserver/webhook"
    signature = validator.compute_signature(webhook_url, webhook_data)

    # --- 3a. DEBUG unset: no [DEBUG_FINAL_OUTPUT] console dump ---
    os.environ.pop("DEBUG", None)
    captured = io.StringIO()
    with contextlib.redirect_stdout(captured):
        client.post("/webhook", data=webhook_data, headers={"X-Twilio-Signature": signature})
    assert "[DEBUG_FINAL_OUTPUT]" not in captured.getvalue(), "DEBUG print occurred even though DEBUG is unset!"
    print("  - DEBUG unset: no [DEBUG_FINAL_OUTPUT] console dump [OK]")

    # --- 3b. DEBUG=true: console dump DOES occur (dev use still works) ---
    os.environ["DEBUG"] = "true"
    captured = io.StringIO()
    with contextlib.redirect_stdout(captured):
        client.post("/webhook", data=webhook_data, headers={"X-Twilio-Signature": signature})
    assert "[DEBUG_FINAL_OUTPUT]" in captured.getvalue(), "DEBUG print did NOT occur even though DEBUG=true!"
    print("  - DEBUG=true: [DEBUG_FINAL_OUTPUT] console dump occurs [OK]")
    os.environ.pop("DEBUG", None)

    # --- 3c/3d. OCR debug file persistence gated behind DEBUG ---
    ok_file = ("bill.jpg", b"fake jpeg bill data", "image/jpeg")
    mock_bill = {
        "doc_type": "SALES_BILL", "items": [{"product": "X", "quantity": 1, "unit": "unit", "unit_price": 1.0, "total": 1.0}],
        "total_amount": 1.0,
    }

    ocr_debug_dir = os.path.join("downloads", "ocr_debug")
    before_count = len(os.listdir(ocr_debug_dir)) if os.path.isdir(ocr_debug_dir) else 0

    os.environ.pop("DEBUG", None)
    with patch("backend.routes.pipeline.parser.parse_local") as mock_parse, \
         patch("backend.routes.pipeline.extractor.extract") as mock_extract:
        mock_parse.return_value = {"file_path": "x", "content_type": "image/jpeg", "text": "ocr text no debug"}
        mock_extract.return_value = mock_bill.copy()
        resp = client.post("/admin/upload/sales-bill", files={"file": ok_file})
    assert resp.status_code == 200
    after_count_nodebug = len(os.listdir(ocr_debug_dir)) if os.path.isdir(ocr_debug_dir) else 0
    assert after_count_nodebug == before_count, "OCR debug file was written even though DEBUG is unset!"
    print("  - DEBUG unset: no OCR debug file written to downloads/ocr_debug/ [OK]")

    os.environ["DEBUG"] = "true"
    with patch("backend.routes.pipeline.parser.parse_local") as mock_parse, \
         patch("backend.routes.pipeline.extractor.extract") as mock_extract:
        mock_parse.return_value = {"file_path": "x", "content_type": "image/jpeg", "text": "ocr text WITH debug"}
        mock_extract.return_value = mock_bill.copy()
        resp = client.post("/admin/upload/sales-bill", files={"file": ok_file})
    assert resp.status_code == 200
    after_count_debug = len(os.listdir(ocr_debug_dir)) if os.path.isdir(ocr_debug_dir) else 0
    assert after_count_debug == before_count + 1, "OCR debug file was NOT written even though DEBUG=true!"
    print("  - DEBUG=true: OCR debug file IS written to downloads/ocr_debug/ [OK]")
    os.environ.pop("DEBUG", None)

    # cleanup the one debug file created
    for f in os.listdir(ocr_debug_dir):
        if "text WITH debug" in open(os.path.join(ocr_debug_dir, f), encoding="utf-8").read():
            os.remove(os.path.join(ocr_debug_dir, f))


try:
    run_tests()
    print("\n" + "=" * 70)
    print("ALL L-1 / L-2 / L-3 TESTS PASSED")
    print("=" * 70 + "\n")
finally:
    if os.path.exists(db.DB_PATH):
        os.remove(db.DB_PATH)
        print("Test database cleaned up successfully.")
