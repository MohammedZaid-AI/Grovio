import os
import sys
from dotenv import load_dotenv
load_dotenv()

# Add root directory to python path
sys.path.append(os.getcwd())

# Override DB path and JWT details before importing FastAPI app
os.environ["JWT_SECRET"] = "test-secret-key-very-secure-length-32-bytes"
os.environ["DASHBOARD_PASSWORD"] = "test-admin-password"

import db
db.DB_PATH = 'database/test_inventory_approval.db'

if os.path.exists(db.DB_PATH):
    os.remove(db.DB_PATH)

db.init_db()

# Seed database with standard recipes & inventory
conn = db.get_connection()
try:
    cursor = conn.cursor()
    # 1. Insert recipe mapping for "Paneer Tikka"
    cursor.execute("""
        INSERT INTO recipes (dish_name, ingredient_name, quantity_per_unit, unit)
        VALUES ('Paneer Tikka', 'paneer', 0.2, 'kg')
    """)
    # 2. Insert recipe mapping for "Butter Naan"
    cursor.execute("""
        INSERT INTO recipes (dish_name, ingredient_name, quantity_per_unit, unit)
        VALUES ('Butter Naan', 'butter', 0.05, 'kg')
    """)
    # 3. Track 'paneer' in inventory with starting balance of 10.0 kg
    cursor.execute("""
        INSERT INTO inventory (product_name, current_stock, minimum_stock, unit)
        VALUES ('paneer', 10.0, 1.0, 'kg')
    """)
    # Note: 'butter' is deliberately left UNTRACKED in inventory
    conn.commit()
finally:
    conn.close()

from fastapi.testclient import TestClient
from backend.app import app

client = TestClient(app)

print("\n=============================================")
print("RUNNING GROVIO INVENTORY APPROVAL INTEGRATION TESTS")
print("=============================================\n")

def test_inventory_approval_flow():
    # Authenticate and get cookie
    login_resp = client.post("/admin/login", data={"password": "test-admin-password"})
    assert login_resp.status_code == 200
    cookies = login_resp.cookies

    # 1. Confirm a Sales Bill
    print("Test 1: Confirming a Sales Bill to stage deductions...")
    # Seed a pending document
    conn = db.get_connection()
    try:
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO pending_documents (phone, doc_type, payload, status)
            VALUES ('+1234567890', 'SALES_BILL', ?, 'PENDING')
        """, (
            '{"invoice_number": "SB-100", "date": "2026-07-07", "total_amount": 500.0, "items": [{"product": "Paneer Tikka", "quantity": 5}, {"product": "Butter Naan", "quantity": 4}]}',
        ))
        doc_id = cursor.lastrowid
        conn.commit()
    finally:
        conn.close()

    # Confirm the document using API
    confirm_resp = client.post(f"/admin/confirm/{doc_id}", cookies=cookies, json={
        "payload": {
            "doc_type": "SALES_BILL",
            "invoice_number": "SB-100",
            "date": "2026-07-07",
            "total_amount": 500.0,
            "items": [
                {"product": "Paneer Tikka", "quantity": 5, "unit_price": 80.0, "total": 400.0},
                {"product": "Butter Naan", "quantity": 4, "unit_price": 25.0, "total": 100.0}
            ]
        }
    })
    assert confirm_resp.status_code == 200
    print("  - Confirmation completed successfully")

    # Verify that inventory stock has NOT been changed immediately
    conn = db.get_connection()
    try:
        cursor = conn.cursor()
        cursor.execute("SELECT current_stock FROM inventory WHERE product_name = 'paneer'")
        paneer_stock = cursor.fetchone()[0]
        print(f"  - Paneer Stock immediately after bill confirm: {paneer_stock} kg (Expected: 10.0)")
        assert paneer_stock == 10.0, "Stock should not change until approved!"

        # Check pending deductions exists in database
        cursor.execute("SELECT id, ingredient_name, estimated_quantity, status FROM pending_inventory_deductions")
        deductions = cursor.fetchall()
        print("  - Staged deductions in DB:", deductions)
        assert len(deductions) == 2, "Expected 2 staged deductions"
        
        # Check product consumption status
        cursor.execute("SELECT product_name, consumed_quantity, status FROM product_consumption")
        consumption = cursor.fetchall()
        print("  - Product consumption in DB:", consumption)
        assert len(consumption) == 2, "Expected 2 consumption logs"
        assert consumption[0][2] == "PENDING"
        assert consumption[1][2] == "PENDING"
    finally:
        conn.close()

    # 2. Get Pending Deductions API
    print("\nTest 2: Querying pending deductions from API...")
    get_resp = client.get("/admin/inventory-deductions/pending", cookies=cookies)
    assert get_resp.status_code == 200
    ded_data = get_resp.json()
    assert len(ded_data) == 2
    # Verify stock status mappings
    paneer_ded = next(d for d in ded_data if d["ingredient_name"] == "paneer")
    butter_ded = next(d for d in ded_data if d["ingredient_name"] == "butter")
    assert paneer_ded["current_stock"] == 10.0
    assert butter_ded["current_stock"] is None  # Untracked
    print("  - API returned correct mapping values")

    # 3. Attempt Approval for Untracked Ingredient (butter) -> Should fail/block
    print("\nTest 3: Testing block on untracked ingredient approval...")
    approve_butter_resp = client.post(f"/admin/inventory-deductions/{butter_ded['id']}/approve", cookies=cookies)
    assert approve_butter_resp.status_code == 400
    print("  - Approval blocked as expected:", approve_butter_resp.json()["message"])

    # 4. Reject Untracked Ingredient (butter) -> Should succeed, consumption status becomes REJECTED
    print("\nTest 4: Rejecting the untracked ingredient deduction...")
    reject_butter_resp = client.post(f"/admin/inventory-deductions/{butter_ded['id']}/reject", cookies=cookies)
    assert reject_butter_resp.status_code == 200
    
    conn = db.get_connection()
    try:
        cursor = conn.cursor()
        # Verify status in pending_inventory_deductions
        cursor.execute("SELECT status FROM pending_inventory_deductions WHERE id = ?", (butter_ded['id'],))
        assert cursor.fetchone()[0] == "REJECTED"
        
        # Verify status in product_consumption
        cursor.execute("SELECT status FROM product_consumption WHERE LOWER(product_name) = 'butter'")
        assert cursor.fetchone()[0] == "REJECTED"
        
        # Verify inventory has no entry for butter
        cursor.execute("SELECT 1 FROM inventory WHERE LOWER(product_name) = 'butter'")
        assert cursor.fetchone() is None
        print("  - Rejection status synced and inventory untouched")
    finally:
        conn.close()

    # 5. Approve Tracked Ingredient (paneer) -> Should succeed, decrement inventory, status becomes APPROVED
    print("\nTest 5: Approving the tracked ingredient deduction...")
    # Quantity to deduct is 5 * 0.2 = 1.0 kg
    approve_paneer_resp = client.post(f"/admin/inventory-deductions/{paneer_ded['id']}/approve", cookies=cookies)
    assert approve_paneer_resp.status_code == 200
    
    conn = db.get_connection()
    try:
        cursor = conn.cursor()
        # Verify inventory stock (10.0 - 1.0 = 9.0 kg)
        cursor.execute("SELECT current_stock FROM inventory WHERE product_name = 'paneer'")
        paneer_stock = cursor.fetchone()[0]
        print(f"  - Paneer Stock after approval: {paneer_stock} kg (Expected: 9.0)")
        assert paneer_stock == 9.0

        # Verify status in pending_inventory_deductions
        cursor.execute("SELECT status FROM pending_inventory_deductions WHERE id = ?", (paneer_ded['id'],))
        assert cursor.fetchone()[0] == "APPROVED"
        
        # Verify status in product_consumption
        cursor.execute("SELECT status FROM product_consumption WHERE LOWER(product_name) = 'paneer'")
        assert cursor.fetchone()[0] == "APPROVED"
        print("  - Stock updated and approval status synced")
    finally:
        conn.close()

    print("\n--- ALL INVENTORY APPROVAL TESTS PASSED ---")

if __name__ == "__main__":
    try:
        test_inventory_approval_flow()
    finally:
        if os.path.exists(db.DB_PATH):
            os.remove(db.DB_PATH)
