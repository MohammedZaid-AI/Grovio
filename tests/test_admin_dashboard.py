import os
import sys
import shutil
from dotenv import load_dotenv
load_dotenv()

# 1. Environment and database path override before imports
os.environ["JWT_SECRET"] = "test-secret-key-very-secure-length-32-bytes"
os.environ["DASHBOARD_PASSWORD"] = "test-admin-password"

import db
db.DB_PATH = 'database/test_orders.db'

# Remove existing test DB if any
if os.path.exists(db.DB_PATH):
    os.remove(db.DB_PATH)

# Initialize isolated test schema
db.init_db()

# Insert dummy recipe for sales bill verification
conn = db.get_connection()
try:
    cursor = conn.cursor()
    # Insert recipes for Chicken Steak and Veg Salad
    cursor.execute("""
        INSERT INTO recipes (dish_name, ingredient_name, quantity_per_unit, unit)
        VALUES ('Chicken Steak', 'chicken', 0.25, 'kg')
    """)
    cursor.execute("""
        INSERT INTO recipes (dish_name, ingredient_name, quantity_per_unit, unit)
        VALUES ('Veg Salad', 'lettuce', 0.1, 'kg')
    """)
    conn.commit()
finally:
    conn.close()

from fastapi.testclient import TestClient
from unittest.mock import patch
from backend.app import app

# https base_url: the session cookie is Secure (see security fix M-1), so a
# plain http TestClient would never receive it back on subsequent requests.
# This matches real deployments, which run behind HTTPS (see CLAUDE.md).
client = TestClient(app, base_url="https://testserver")

print("\n=============================================")
print("RUNNING GROVIO ADMIN DASHBOARD INTEGRATION TESTS")
print("=============================================\n")

def run_tests():
    # -------------------------------------------------------
    # Test A: Unauthenticated Access
    # -------------------------------------------------------
    print("Test A: Verifying Unauthenticated Access Protection...")
    
    # 1. API route returns 401
    resp = client.get("/admin/pending-documents")
    assert resp.status_code == 401, f"Expected 401, got {resp.status_code}"
    print("  - GET /admin/pending-documents returns 401 [OK]")

    # 2. Page route redirects to /admin/login
    resp = client.get("/admin", follow_redirects=False)
    assert resp.status_code == 303, f"Expected 303 Redirect, got {resp.status_code}"
    assert resp.headers["location"] == "/admin/login", f"Expected location /admin/login, got {resp.headers.get('location')}"
    print("  - GET /admin redirects to /admin/login [OK]")
    
    # -------------------------------------------------------
    # Test B: Authentication Flow (Login/Logout)
    # -------------------------------------------------------
    print("\nTest B: Verifying Authentication Flow...")
    
    # 1. Login with incorrect password
    resp = client.post("/admin/login", data={"password": "wrong-password"})
    assert resp.status_code == 401, f"Expected 401, got {resp.status_code}"
    assert resp.json()["success"] is False, "Expected success to be False"
    print("  - POST /admin/login with incorrect password returns 401 [OK]")

    # 2. Login with correct password
    resp = client.post("/admin/login", data={"password": "test-admin-password"})
    assert resp.status_code == 200, f"Expected 200, got {resp.status_code}"
    assert resp.json()["success"] is True, "Expected success to be True"
    assert "session_token" in client.cookies, "Expected session_token cookie to be set"
    print("  - POST /admin/login with correct password sets cookie [OK]")

    # 3. Access dashboard page after auth
    resp = client.get("/admin", follow_redirects=False)
    assert resp.status_code == 200, f"Expected 200, got {resp.status_code}"
    print("  - GET /admin returns dashboard HTML [OK]")

    # -------------------------------------------------------
    # Test C: File Upload Safety (Size & Type Limits)
    # -------------------------------------------------------
    print("\nTest C: Verifying File Upload Safety Checks...")
    
    # 1. Reject invalid file type
    bad_file = ("test.txt", b"dummy txt content", "text/plain")
    resp = client.post("/admin/upload/sales-bill", files={"file": bad_file})
    assert resp.status_code == 400, f"Expected 400, got {resp.status_code}"
    assert "only pdf and jpeg/png" in resp.json()["message"].lower(), f"Expected error message, got: {resp.json().get('message')}"
    print("  - Uploading txt file rejected with 400 [OK]")

    # 2. Reject file larger than 10MB
    large_content = b"0" * (10 * 1024 * 1024 + 100) # 10MB + 100 bytes
    large_file = ("large.pdf", large_content, "application/pdf")
    resp = client.post("/admin/upload/sales-bill", files={"file": large_file})
    assert resp.status_code == 400, f"Expected 400, got {resp.status_code}"
    assert "File too large" in resp.json()["message"], f"Expected error message, got: {resp.json().get('message')}"
    print("  - Uploading >10MB file rejected with 400 [OK]")

    # -------------------------------------------------------
    # Test D: Upload Ingestion, OCR & Parsing Cleanup
    # -------------------------------------------------------
    print("\nTest D: Verifying OCR Upload & Temp File Cleanup...")
    
    mock_sales_bill = {
        "doc_type": "SALES_BILL",
        "supplier": "",
        "invoice_number": "SB-9999",
        "date": "2026-07-05",
        "items": [
            {"product": "Chicken Steak", "quantity": 4, "unit": "unit", "unit_price": 200.0, "total": 800.0},
            {"product": "Veg Salad", "quantity": 2, "unit": "unit", "unit_price": 50.0, "total": 100.0}
        ],
        "total_amount": 900.0
    }

    mock_supplier_invoice = {
        "doc_type": "SUPPLIER_INVOICE",
        "supplier": "ABC Dairy",
        "invoice_number": "INV-8888",
        "date": "2026-07-05",
        "items": [
            {"product": "Milk", "quantity": 10.0, "unit": "liters", "unit_price": 50.0, "total": 500.0}
        ],
        "total_amount": 500.0
    }

    # Prepare mock upload files
    ok_file_bill = ("bill.jpg", b"fake jpeg bill data", "image/jpeg")
    ok_file_invoice = ("invoice.pdf", b"fake pdf invoice data", "application/pdf")

    # Upload Sales Bill
    with patch("backend.routes.pipeline.parser.parse_local") as mock_parse, \
         patch("backend.routes.pipeline.extractor.extract") as mock_extract:
        
        mock_parse.return_value = {"file_path": "downloads/web_test_bill.jpg", "content_type": "image/jpeg", "text": "bill ocr text"}
        mock_extract.return_value = mock_sales_bill.copy()

        resp = client.post("/admin/upload/sales-bill", files={"file": ok_file_bill})
        assert resp.status_code == 200, f"Expected 200, got {resp.status_code}"
        res_json = resp.json()
        assert res_json["success"] is True
        bill_doc_id = res_json["id"]
        print(f"  - Uploaded Sales Bill successfully (Pending Doc ID: {bill_doc_id}) [OK]")

    # Check that temp file directory contains no leaked "web_" files
    leaked_files = [f for f in os.listdir("downloads") if f.startswith("web_")]
    assert len(leaked_files) == 0, f"Leaked temporary files found: {leaked_files}"
    print("  - Temp files successfully cleaned up in finally block [OK]")

    # Upload Supplier Invoice
    with patch("backend.routes.pipeline.parser.parse_local") as mock_parse, \
         patch("backend.routes.pipeline.extractor.extract") as mock_extract:
        
        mock_parse.return_value = {"file_path": "downloads/web_test_invoice.pdf", "content_type": "application/pdf", "text": "invoice pdf text"}
        mock_extract.return_value = mock_supplier_invoice.copy()

        resp = client.post("/admin/upload/grocery-invoice", files={"file": ok_file_invoice})
        assert resp.status_code == 200, f"Expected 200, got {resp.status_code}"
        res_json = resp.json()
        assert res_json["success"] is True
        invoice_doc_id = res_json["id"]
        print(f"  - Uploaded Supplier Invoice successfully (Pending Doc ID: {invoice_doc_id}) [OK]")

    # Verify pending document retrieval endpoint
    resp = client.get("/admin/pending-documents")
    assert resp.status_code == 200, f"Expected 200, got {resp.status_code}"
    pending_docs = resp.json()
    assert len(pending_docs) == 2, f"Expected 2 pending documents, got {len(pending_docs)}"
    print("  - GET /admin/pending-documents lists both uploaded documents [OK]")

    # -------------------------------------------------------
    # Test E: Confirmation and Rejection Processing
    # -------------------------------------------------------
    print("\nTest E: Verifying Confirmation & Rejection Flows...")

    # 1. Reject the Sales Bill upload
    resp = client.post(f"/admin/reject/{bill_doc_id}")
    assert resp.status_code == 200, f"Expected 200, got {resp.status_code}"
    assert resp.json()["success"] is True
    
    # Verify status is CANCELLED in DB
    pending_doc = db.get_pending_document_by_id(bill_doc_id)
    assert pending_doc["status"] == "CANCELLED", f"Expected CANCELLED status, got {pending_doc['status']}"
    print("  - Rejected Sales Bill marked as CANCELLED in database [OK]")

    # Re-upload Sales Bill to confirm it in next step
    with patch("backend.routes.pipeline.parser.parse_local") as mock_parse, \
         patch("backend.routes.pipeline.extractor.extract") as mock_extract:
        mock_parse.return_value = {"file_path": "downloads/web_test_bill.jpg", "content_type": "image/jpeg", "text": "bill ocr text"}
        mock_extract.return_value = mock_sales_bill.copy()
        resp = client.post("/admin/upload/sales-bill", files={"file": ok_file_bill})
        new_bill_doc_id = resp.json()["id"]

    # 2. Confirm the Sales Bill (triggers consumption recalculation)
    resp = client.post(f"/admin/confirm/{new_bill_doc_id}")
    assert resp.status_code == 200, f"Expected 200, got {resp.status_code}"
    assert resp.json()["success"] is True
    print("  - Confirmed Sales Bill successfully [OK]")

    # Verify database updates for confirmed sales bill (recipes & consumption)
    conn = db.get_connection()
    try:
        cursor = conn.cursor()
        # Verify sales_bills table
        cursor.execute("SELECT id, bill_number, total_amount, status FROM sales_bills WHERE bill_number = 'SB-9999'")
        sb_row = cursor.fetchone()
        assert sb_row is not None, "Sales bill row should be saved in DB"
        assert sb_row[3] == "CONFIRMED", f"Expected status CONFIRMED, got {sb_row[3]}"
        sb_id = sb_row[0]

        # Verify sales_bill_items table
        cursor.execute("SELECT dish_name, quantity FROM sales_bill_items WHERE bill_id = ?", (sb_id,))
        sb_items = cursor.fetchall()
        assert len(sb_items) == 2, "Expected 2 items saved in sales_bill_items"

        # Verify product_consumption table (quantity sold * recipe quantity per unit)
        # Chicken Steak: 4 sold * 0.25 kg = 1.0 kg chicken
        cursor.execute("SELECT consumed_quantity, unit FROM product_consumption WHERE product_name = 'chicken' AND source_bill_id = ?", (sb_id,))
        chicken_cons = cursor.fetchone()
        assert chicken_cons is not None, "Chicken consumption should be recorded"
        assert chicken_cons[0] == 1.0, f"Expected 1.0 kg chicken consumed, got {chicken_cons[0]}"

        # Veg Salad: 2 sold * 0.1 kg = 0.2 kg lettuce
        cursor.execute("SELECT consumed_quantity, unit FROM product_consumption WHERE product_name = 'lettuce' AND source_bill_id = ?", (sb_id,))
        lettuce_cons = cursor.fetchone()
        assert lettuce_cons is not None, "Lettuce consumption should be recorded"
        assert lettuce_cons[0] == 0.2, f"Expected 0.2 kg lettuce consumed, got {lettuce_cons[0]}"
        print("  - Database verification: sales_bills, sales_bill_items, product_consumption updated correctly [OK]")
    finally:
        conn.close()

    # 3. Confirm the Supplier Invoice (triggers inventory updates)
    resp = client.post(f"/admin/confirm/{invoice_doc_id}")
    assert resp.status_code == 200, f"Expected 200, got {resp.status_code}"
    assert resp.json()["success"] is True
    print("  - Confirmed Supplier Invoice successfully [OK]")

    # Verify database updates for confirmed supplier invoice (purchase_invoices, inventory)
    conn = db.get_connection()
    try:
        cursor = conn.cursor()
        # Verify purchase_invoices
        cursor.execute("SELECT id, supplier, invoice_number, total_amount FROM purchase_invoices WHERE invoice_number = 'INV-8888'")
        pi_row = cursor.fetchone()
        assert pi_row is not None, "Purchase invoice row should be saved in DB"
        
        # Verify purchase_items
        cursor.execute("SELECT product, quantity, unit_price FROM purchase_items WHERE invoice_id = ?", (pi_row[0],))
        pi_items = cursor.fetchall()
        assert len(pi_items) == 1, "Expected 1 item saved in purchase_items"
        assert pi_items[0][0] == "Milk", f"Expected Milk, got {pi_items[0][0]}"

        # Verify inventory update (Milk current stock should be 10)
        cursor.execute("SELECT current_stock, unit FROM inventory WHERE LOWER(product_name) = 'milk'")
        milk_inv = cursor.fetchone()
        assert milk_inv is not None, "Milk inventory row should exist"
        assert milk_inv[0] == 10.0, f"Expected 10.0 liters stock, got {milk_inv[0]}"
        print("  - Database verification: purchase_invoices, purchase_items, inventory updated correctly [OK]")
    finally:
        conn.close()

    # 4. Logout
    client.post("/admin/logout")
    assert "session_token" not in client.cookies or client.cookies.get("session_token") == "", "Expected cookie to be cleared"
    print("  - Logout clears session cookie [OK]")

    # Verify access is blocked again
    resp = client.get("/admin/pending-documents")
    assert resp.status_code == 401, f"Expected 401, got {resp.status_code}"
    print("  - Access blocked post-logout [OK]")

    # 5. Verify WhatsApp Webhook: signature verification + image redirection
    print("\nTest F: Verifying WhatsApp Webhook Signature Verification & Image Redirection...")
    old_token = os.environ.get("TWILIO_AUTH_TOKEN")

    webhook_data = {
        "NumMedia": "1",
        "MediaUrl0": "http://example.com/invoice.jpg",
        "MediaContentType0": "image/jpeg",
        "From": "+1234567890"
    }

    try:
        # 5a. SECURITY (M-2): missing TWILIO_AUTH_TOKEN must fail CLOSED (500),
        # never silently skip verification and process the request anyway.
        if "TWILIO_AUTH_TOKEN" in os.environ:
            del os.environ["TWILIO_AUTH_TOKEN"]
        resp = client.post("/webhook", data=webhook_data)
        assert resp.status_code == 500, f"Expected 500 (fail closed), got {resp.status_code}"
        print("  - Missing TWILIO_AUTH_TOKEN rejects webhook with 500 (fail closed) [OK]")

        # 5b. A request with an INVALID signature must be rejected (403).
        os.environ["TWILIO_AUTH_TOKEN"] = "test-twilio-auth-token"
        resp = client.post("/webhook", data=webhook_data, headers={"X-Twilio-Signature": "not-a-real-signature"})
        assert resp.status_code == 403, f"Expected 403, got {resp.status_code}"
        print("  - Invalid Twilio signature rejected with 403 [OK]")

        # 5c. A request with a CORRECTLY signed payload must still succeed end-to-end.
        from twilio.request_validator import RequestValidator
        validator = RequestValidator(os.environ["TWILIO_AUTH_TOKEN"])
        webhook_url = "http://testserver/webhook"  # matches routes.py's url construction for this TestClient
        valid_signature = validator.compute_signature(webhook_url, webhook_data)

        resp = client.post("/webhook", data=webhook_data, headers={"X-Twilio-Signature": valid_signature})
        assert resp.status_code == 200, f"Expected 200, got {resp.status_code}"
        assert "Grovio Document Upload" in resp.text, "Expected dashboard redirect info in reply"
        assert "/admin" in resp.text, "Expected dashboard URL in redirect reply"
        print("  - Correctly signed request: WhatsApp media redirected to dashboard URL [OK]")
    finally:
        if old_token:
            os.environ["TWILIO_AUTH_TOKEN"] = old_token
        elif "TWILIO_AUTH_TOKEN" in os.environ:
            del os.environ["TWILIO_AUTH_TOKEN"]

    # 6. Verify Self-Correcting LLM Extraction & Duplicate Items
    print("\nTest G: Verifying Self-Correcting LLM Extraction & Duplicate Items...")
    from ai.invoice.extractor import InvoiceExtractor
    extractor = InvoiceExtractor()

    # Case 1: Simulated raw OCR with duplicate items and prefix-merged values
    simulated_ocr_with_merges = """
INV-2026-0423
Date: 2026-02-12
Masala Dosa 4 2499 21996
Masala Dosa 2 2499
Paneer Butter Masala 2 2399
Paneer Butter Masala 6
Cold Coffee 6 80 480
Masala Dosa 4 80 320
Total: 5186
Tax: 259.3
Net Total: 5445.3
"""
    res1 = extractor.extract(simulated_ocr_with_merges)
    items1 = res1.get("items", [])
    
    assert len(items1) == 6, f"Expected 6 items, got {len(items1)}"
    
    # Masala Dosa 4 2499 21996
    assert items1[0]["product"] == "Masala Dosa"
    assert items1[0]["quantity"] == 4
    assert items1[0]["unit_price"] == 2499  # Preserves raw value!
    assert items1[0]["total"] == 21996  # Preserves raw value!
    assert items1[0]["is_inconsistent"] is True  # Flagged!
    assert items1[0]["suggested_correction"]["unit_price"] == 499
    assert items1[0]["suggested_correction"]["total"] == 1996
    
    # Masala Dosa 2 2499
    assert items1[1]["product"] == "Masala Dosa"
    assert items1[1]["quantity"] == 2
    assert items1[1]["unit_price"] == 2499  # Preserves raw value!
    assert items1[1]["is_inconsistent"] is True  # Flagged!
    assert items1[1]["suggested_correction"]["unit_price"] == 499
    assert items1[1]["suggested_correction"]["total"] == 998
    
    # Paneer Butter Masala 2 2399
    assert items1[2]["product"] == "Paneer Butter Masala"
    assert items1[2]["quantity"] == 2
    assert items1[2]["unit_price"] == 2399  # Preserves raw value!
    assert items1[2]["is_inconsistent"] is True  # Flagged!
    assert items1[2]["suggested_correction"]["unit_price"] == 399
    assert items1[2]["suggested_correction"]["total"] == 798
    
    # Paneer Butter Masala 6 (solved via subtraction solver!)
    assert items1[3]["product"] == "Paneer Butter Masala"
    assert items1[3]["quantity"] == 6
    assert items1[3]["unit_price"] is None  # Preserves raw value!
    assert items1[3]["is_inconsistent"] is True  # Flagged!
    assert items1[3]["suggested_correction"]["unit_price"] == 99
    assert items1[3]["suggested_correction"]["total"] == 594
    
    # Cold Coffee 6 80 480 (Consistent!)
    assert items1[4]["product"] == "Cold Coffee"
    assert items1[4]["quantity"] == 6
    assert items1[4]["unit_price"] == 80
    assert items1[4]["total"] == 480
    assert items1[4]["is_inconsistent"] is False  # Not flagged!
    assert items1[4]["suggested_correction"] is None

    # Masala Dosa 4 80 320 (Consistent!)
    assert items1[5]["product"] == "Masala Dosa"
    assert items1[5]["quantity"] == 4
    assert items1[5]["unit_price"] == 80
    assert items1[5]["total"] == 320
    assert items1[5]["is_inconsistent"] is False  # Not flagged!
    assert items1[5]["suggested_correction"] is None

    print("  - Case 1 (User receipt duplicates & merged prices) resolved successfully [OK]")

    # Case 2: Different mock receipt with other duplicate items (Chicken Biryani, Butter Naan)
    other_ocr_with_merges = """
BILL-777
Date: 2026-07-05
Chicken Biryani 3 3299 3897
Chicken Biryani 2 2299
Butter Naan 5 560 300
Butter Naan 2 260
Total: 1914
Tax: 95.7
Net Total: 2009.7
"""
    res2 = extractor.extract(other_ocr_with_merges)
    items2 = res2.get("items", [])
    
    assert len(items2) == 4, f"Expected 4 items, got {len(items2)}"
    
    # Chicken Biryani 3 3299 3897
    assert items2[0]["product"] == "Chicken Biryani"
    assert items2[0]["quantity"] == 3
    assert items2[0]["unit_price"] == 3299  # Preserves raw!
    assert items2[0]["is_inconsistent"] is True
    assert items2[0]["suggested_correction"]["unit_price"] == 299
    assert items2[0]["suggested_correction"]["total"] == 897
    
    # Chicken Biryani 2 2299
    assert items2[1]["product"] == "Chicken Biryani"
    assert items2[1]["quantity"] == 2
    assert items2[1]["unit_price"] == 2299
    assert items2[1]["is_inconsistent"] is True
    assert items2[1]["suggested_correction"]["unit_price"] == 299
    assert items2[1]["suggested_correction"]["total"] == 598

    # Butter Naan 5 560 300
    assert items2[2]["product"] == "Butter Naan"
    assert items2[2]["quantity"] == 5
    assert items2[2]["unit_price"] == 560
    assert items2[2]["total"] == 300
    assert items2[2]["is_inconsistent"] is True
    assert items2[2]["suggested_correction"]["unit_price"] == 60
    assert items2[2]["suggested_correction"]["total"] == 300

    # Butter Naan 2 260
    assert items2[3]["product"] == "Butter Naan"
    assert items2[3]["quantity"] == 2
    assert items2[3]["unit_price"] == 260
    assert items2[3]["is_inconsistent"] is True
    assert items2[3]["suggested_correction"]["unit_price"] == 60
    assert items2[3]["suggested_correction"]["total"] == 120
    
    print("  - Case 2 (Alternative mock receipt duplicates) resolved successfully [OK]")

    # Case 3: Dropped Digit / Misplaced Decimal
    dropped_digit_ocr = """
INV-999
Date: 2026-07-05
Paneer Butter Masala 2 39.9 798
Total: 798
Net Total: 798
"""
    res3 = extractor.extract(dropped_digit_ocr)
    items3 = res3.get("items", [])
    assert len(items3) == 1
    assert items3[0]["product"] == "Paneer Butter Masala"
    assert items3[0]["quantity"] == 2
    assert items3[0]["unit_price"] == 39.9  # Raw value!
    assert items3[0]["total"] == 798.0  # Raw value!
    assert items3[0]["is_inconsistent"] is True
    assert items3[0]["suggested_correction"]["unit_price"] == 399.0  # Mathematically suggested!
    assert items3[0]["suggested_correction"]["total"] == 798.0
    print("  - Case 3 (Dropped digit / misplaced decimal validation) resolved successfully [OK]")

    # Case 4: Two Missing Rows (ambiguity cannot be solved, flags both as inconsistent but doesn't crash)
    two_missing_rows_ocr = """
INV-555
Date: 2026-07-05
Cold Coffee 6
Masala Dosa 4
Total: 800
Net Total: 800
"""
    res4 = extractor.extract(two_missing_rows_ocr)
    items4 = res4.get("items", [])
    assert len(items4) == 2
    assert items4[0]["product"] == "Cold Coffee"
    assert items4[0]["quantity"] == 6
    assert items4[0]["unit_price"] in (None, 0.0)
    assert items4[0]["total"] in (None, 0.0)
    assert items4[0]["is_inconsistent"] is True  # Flagged!
    
    assert items4[1]["product"] == "Masala Dosa"
    assert items4[1]["quantity"] == 4
    assert items4[1]["unit_price"] in (None, 0.0)
    assert items4[1]["total"] in (None, 0.0)
    assert items4[1]["is_inconsistent"] is True  # Flagged!
    print("  - Case 4 (Two missing rows flagged safely without crashing) resolved successfully [OK]")


# Run tests and clean up
try:
    run_tests()
    print("\n=============================================")
    print("ALL TESTS PASSED SUCCESSFULLY! (INTEGRATION OK)")
    print("=============================================\n")
finally:
    # Cleanup test database
    if os.path.exists(db.DB_PATH):
        os.remove(db.DB_PATH)
        print("Test database cleaned up successfully.")
