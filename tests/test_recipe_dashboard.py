import os
import sys
from dotenv import load_dotenv
load_dotenv()

sys.path.append(os.getcwd())

# Override DB path and JWT details before importing FastAPI app
os.environ["JWT_SECRET"] = "test-secret-key-very-secure-length-32-bytes"
os.environ["DASHBOARD_PASSWORD"] = "test-admin-password"

import db
db.DB_PATH = 'database/test_recipes.db'

if os.path.exists(db.DB_PATH):
    os.remove(db.DB_PATH)

db.init_db()

from fastapi.testclient import TestClient
from backend.app import app

client = TestClient(app)

print("\n=============================================")
print("RUNNING GROVIO ADMIN RECIPE INTEGRATION TESTS")
print("=============================================\n")

def test_recipes_pipeline():
    # 1. Unauthenticated checks
    print("Test 1: Verifying unauthenticated access is blocked...")
    get_resp = client.get("/admin/recipes")
    assert get_resp.status_code == 401

    post_resp = client.post("/admin/recipes", json={})
    assert post_resp.status_code == 401

    delete_resp = client.post("/admin/recipes/delete", json={})
    assert delete_resp.status_code == 401
    print("  - Unauthenticated block OK")

    # Helper to authenticate and get cookie
    login_resp = client.post("/admin/login", data={"password": "test-admin-password"})
    assert login_resp.status_code == 200, "Should log in successfully"
    cookies = login_resp.cookies

    # 2. Validation checks
    print("\nTest 2: Verifying payload validations...")
    # Missing dish name
    resp = client.post("/admin/recipes", cookies=cookies, json={
        "ingredients": [{"ingredient_name": "chicken", "quantity_per_unit": 0.25, "unit": "kg"}]
    })
    assert resp.status_code == 400
    assert "Dish name is required" in resp.json()["message"]

    # Missing ingredients list
    resp = client.post("/admin/recipes", cookies=cookies, json={
        "dish_name": "Chicken Steak"
    })
    assert resp.status_code == 400
    assert "At least one ingredient is required" in resp.json()["message"]

    # Negative quantity
    resp = client.post("/admin/recipes", cookies=cookies, json={
        "dish_name": "Chicken Steak",
        "ingredients": [{"ingredient_name": "chicken", "quantity_per_unit": -0.5, "unit": "kg"}]
    })
    assert resp.status_code == 400
    assert "Quantity must be a positive number" in resp.json()["message"]
    print("  - Validation checks OK")

    # 3. Add Recipe
    print("\nTest 3: Creating a new recipe...")
    recipe_payload = {
        "dish_name": "Paneer Butter Masala",
        "ingredients": [
            {"ingredient_name": "paneer", "quantity_per_unit": 0.2, "unit": "kg"},
            {"ingredient_name": "butter", "quantity_per_unit": 0.05, "unit": "kg"}
        ]
    }
    resp = client.post("/admin/recipes", cookies=cookies, json=recipe_payload)
    assert resp.status_code == 200
    assert resp.json()["success"] is True
    print("  - Recipe created successfully")

    # 4. Fetch Recipes
    print("\nTest 4: Retrieving recipes...")
    resp = client.get("/admin/recipes", cookies=cookies)
    assert resp.status_code == 200
    data = resp.json()
    assert "Paneer Butter Masala" in data
    items = data["Paneer Butter Masala"]
    assert len(items) == 2
    items.sort(key=lambda x: x["ingredient_name"])
    assert items[0]["ingredient_name"] == "butter"
    assert items[0]["quantity_per_unit"] == 0.05
    assert items[1]["ingredient_name"] == "paneer"
    assert items[1]["quantity_per_unit"] == 0.2
    print("  - Recipe format and content retrieval OK")

    # 5. Delete Recipe
    print("\nTest 5: Deleting recipe...")
    resp = client.post("/admin/recipes/delete", cookies=cookies, json={"dish_name": "Paneer Butter Masala"})
    assert resp.status_code == 200
    assert resp.json()["success"] is True

    # Re-fetch and verify list is empty
    resp = client.get("/admin/recipes", cookies=cookies)
    assert resp.status_code == 200
    assert "Paneer Butter Masala" not in resp.json()
    print("  - Recipe deletion verified OK")

    print("\n--- ALL RECIPE TESTS PASSED ---")

if __name__ == "__main__":
    try:
        test_recipes_pipeline()
    finally:
        if os.path.exists(db.DB_PATH):
            os.remove(db.DB_PATH)
