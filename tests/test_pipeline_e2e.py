"""
End-to-end: inventory add -> list -> recipes -> customer bill -> deduction ->
stock update -> low-stock. Drives the REAL API routes (TestClient) + db helpers
with the realistic seed data from the stabilization brief. Verifies math.
"""
import os, sys, tempfile, json
sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("JWT_SECRET", "x" * 48)
os.environ.setdefault("DASHBOARD_PASSWORD", "pw")

import db
_tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False); _tmp.close()
db.DB_PATH = _tmp.name; db.init_db()

from fastapi.testclient import TestClient
from backend.app import app

_ok = _fail = 0
def check(name, cond, extra=""):
    global _ok, _fail
    if cond: _ok += 1; print(f"  ✅ {name}")
    else: _fail += 1; print(f"  ❌ {name}   {extra}")

with TestClient(app, base_url="https://testserver") as c:   # Secure cookie needs https
    c.post("/admin/login", data={"password": "pw"})   # cookie stored on client

    # ---- Issue 1: inventory add + list ----
    print("\n[1] Inventory add + list refresh")
    seed = [("Tomato",25,5,"kg"),("Onion",30,5,"kg"),("Rice",50,10,"kg"),
            ("Chicken",20,4,"kg"),("Oil",15,3,"L"),("Butter",5,1,"kg"),
            ("Paneer",8,2,"kg"),("Eggs",150,30,"pcs"),("Milk",20,5,"L")]
    for name, stock, mn, unit in seed:
        r = c.post("/admin/inventory/add", json={"product_name": name, "current_stock": stock,
                                                 "minimum_stock": mn, "unit": unit})
        check(f"add {name}", r.status_code == 200 and r.json().get("success"), r.text)

    inv = c.get("/admin/inventory").json()["data"]
    by_name = {row["product_name"]: row for row in inv}
    check("all 9 items listed", len(inv) == 9, f"got {len(inv)}")
    check("Rice stock persisted = 50", by_name.get("Rice", {}).get("current_stock") == 50)

    # ---- Issue 2/3: recipes create + list + link by ingredient name ----
    print("\n[2] Recipes create + list")
    recipes = {
        "Chicken Biryani": [("Rice",0.15,"kg"),("Chicken",0.25,"kg"),("Tomato",0.1,"kg"),
                            ("Onion",0.1,"kg"),("Oil",0.05,"L")],
        "Butter Chicken":  [("Chicken",0.2,"kg"),("Butter",0.05,"kg"),("Tomato",0.15,"kg"),
                            ("Onion",0.08,"kg")],
        "Paneer Butter Masala": [("Paneer",0.2,"kg"),("Butter",0.04,"kg"),("Tomato",0.12,"kg")],
        "Tea": [("Milk",0.1,"L"),("Eggs",0,"pcs")],  # 0-qty ingredient intentionally
    }
    for dish, ings in recipes.items():
        payload = {"dish_name": dish,
                   "ingredients": [{"ingredient_name": n, "quantity_per_unit": q, "unit": u}
                                   for n, q, u in ings if q > 0]}
        r = c.post("/admin/recipes", json=payload)
        check(f"save recipe {dish}", r.status_code == 200 and r.json().get("success"), r.text)

    listed = c.get("/admin/recipes").json()
    check("active recipes returned (dict keyed by dish)", isinstance(listed, dict) and len(listed) == 4,
          f"got {list(listed)[:5]}")
    check("Chicken Biryani links 5 ingredients", len(listed.get("Chicken Biryani", [])) == 5)
    # save_recipe normalizes ingredient names (get_base_product); linkage to
    # inventory is by name, matched case-insensitively at deduction time.
    inv_lower = {k.lower() for k in by_name}
    check("recipe ingredients resolve to inventory (case-insensitive)",
          all(i["ingredient_name"].lower() in inv_lower for i in listed["Chicken Biryani"]))

    # ---- Issue 4: customer bill -> deduction (2 Chicken Biryani) ----
    print("\n[3] Customer bill -> recipe consumption -> deductions")
    bill_payload = {"doc_type": "SALES_BILL", "invoice_number": "BILL-001",
                    "date": "2026-07-20", "total_amount": 700,
                    "items": [{"product": "Chicken Biryani", "quantity": 2,
                               "unit_price": 350, "total": 700}]}
    doc_id = db.save_pending_document("web_test", "SALES_BILL", bill_payload)
    r = c.post(f"/admin/confirm/{doc_id}", json={})
    check("confirm sales bill", r.status_code == 200 and r.json().get("success"), r.text)

    deds = c.get("/admin/inventory-deductions/pending").json()
    dmap = {d["ingredient_name"].lower(): d for d in deds}   # names normalized lowercase
    expect = {"rice":0.3, "chicken":0.5, "tomato":0.2, "onion":0.2, "oil":0.1}   # 2 x recipe
    check("5 deductions created", len(deds) == 5, f"got {len(deds)}: {list(dmap)}")
    for ing, q in expect.items():
        got = dmap.get(ing, {}).get("estimated_quantity")
        check(f"deduction {ing} = {q}", got is not None and abs(got - q) < 1e-9, f"got {got}")

    # ---- Deduct -> stock update ----
    print("\n[4] Approve deductions -> stock decremented (math)")
    before = {ing: by_name[ing.capitalize()]["current_stock"] for ing in expect}
    for d in deds:
        rr = c.post(f"/admin/inventory-deductions/{d['id']}/approve")
        check(f"approve {d['ingredient_name']}", rr.status_code == 200 and rr.json().get("success"), rr.text)

    inv2 = {row["product_name"]: row for row in c.get("/admin/inventory").json()["data"]}
    for ing, q in expect.items():
        exp_stock = before[ing] - q
        got = inv2[ing.capitalize()]["current_stock"]
        check(f"{ing}: {before[ing]} - {q} = {exp_stock}", abs(got - exp_stock) < 1e-9, f"got {got}")

    # ---- Low stock detection ----
    print("\n[5] Low-stock detection")
    # Chicken min=4, now 19.5 -> ok. Drive one below min and re-check.
    c.post("/admin/inventory/update", json={"product_name": "Butter", "new_stock": 0.5,
                                            "action_type": "SET_ABSOLUTE"})
    low = {r[1] for r in db.get_low_stock_items()}   # r[1] = product_name
    check("Butter (0.5 <= min 1) flagged low", "Butter" in low, f"low={low}")
    check("Rice (49.7 > min 10) not low", "Rice" not in low)
    # Multiple low products at once
    c.post("/admin/inventory/update", json={"product_name": "Oil", "new_stock": 2, "action_type": "SET_ABSOLUTE"})
    low2 = {r[1] for r in db.get_low_stock_items()}
    check("multiple low products detected (Butter + Oil)", {"Butter", "Oil"} <= low2, f"low={low2}")

    # ---- Error handling (no silent failures) ----
    print("\n[6] Error handling")
    r = c.post("/admin/inventory/add", json={"product_name": "Bad", "current_stock": -5,
                                             "minimum_stock": 1, "unit": "kg"})
    check("negative stock rejected 400", r.status_code == 400 and not r.json()["success"], r.text)
    r = c.post("/admin/inventory/add", json={"product_name": "Bad", "current_stock": "abc",
                                             "minimum_stock": 1, "unit": "kg"})
    check("non-numeric stock rejected 400", r.status_code == 400)
    r = c.post("/admin/inventory/add", json={"product_name": "", "current_stock": 1,
                                             "minimum_stock": 1, "unit": "kg"})
    check("empty product name rejected 400", r.status_code == 400)
    r = c.post("/admin/inventory/update", json={"product_name": "Rice", "new_stock": -1000,
                                                "action_type": "ADJUST_DELTA"})
    check("adjustment below zero rejected 400", r.status_code == 400)
    # Adding an EXISTING product ACCUMULATES (add-to-existing), never a 2nd row.
    before_rice = next(x for x in c.get("/admin/inventory").json()["data"] if x["product_name"] == "Rice")["current_stock"]
    c.post("/admin/inventory/add", json={"product_name": "Rice", "current_stock": 99, "minimum_stock": 10, "unit": "kg"})
    rice_rows = [x for x in c.get("/admin/inventory").json()["data"] if x["product_name"] == "Rice"]
    check("existing add accumulates (one Rice row, +99)",
          len(rice_rows) == 1 and abs(rice_rows[0]["current_stock"] - (before_rice + 99)) < 1e-9, f"{rice_rows}")
    # Case-VARIANT add accumulates into the same canonical row, never a twin.
    before2 = rice_rows[0]["current_stock"]
    c.post("/admin/inventory/add", json={"product_name": "rice", "current_stock": 77, "minimum_stock": 10, "unit": "kg"})
    rv = [x for x in c.get("/admin/inventory").json()["data"] if x["product_name"].lower() == "rice"]
    check("case-variant add: single canonical 'Rice' row, +77",
          len(rv) == 1 and rv[0]["product_name"] == "Rice" and abs(rv[0]["current_stock"] - (before2 + 77)) < 1e-9, f"{rv}")
    # Recipe with empty/invalid fields
    r = c.post("/admin/recipes", json={"dish_name": "Bad", "ingredients": []})
    check("recipe with no ingredients rejected 400", r.status_code == 400)
    r = c.post("/admin/recipes", json={"dish_name": "Bad",
               "ingredients": [{"ingredient_name": "X", "quantity_per_unit": 0, "unit": "kg"}]})
    check("recipe with zero quantity rejected 400", r.status_code == 400)
    # Deduction for an UNTRACKED ingredient is blocked gracefully.
    c.post("/admin/recipes", json={"dish_name": "Mystery",
           "ingredients": [{"ingredient_name": "Unobtainium", "quantity_per_unit": 1, "unit": "kg"}]})
    mid = db.save_pending_document("web_m", "SALES_BILL",
          {"doc_type": "SALES_BILL", "invoice_number": "B2", "date": "2026-07-20", "total_amount": 1,
           "items": [{"product": "Mystery", "quantity": 1, "unit_price": 1, "total": 1}]})
    c.post(f"/admin/confirm/{mid}", json={})
    untracked = [d for d in c.get("/admin/inventory-deductions/pending").json()
                 if d["ingredient_name"].lower() == "unobtainium"]
    check("untracked ingredient deduction created but current_stock=None", untracked and untracked[0]["current_stock"] is None)
    ar = c.post(f"/admin/inventory-deductions/{untracked[0]['id']}/approve")
    check("approving untracked ingredient blocked with message",
          ar.status_code == 400 and "not tracked" in ar.json()["message"].lower(), ar.text)

    # ---- Adjust-by-delta (was a silent no-op: FE sent delta_quantity, BE read new_stock) ----
    print("\n[7] Inventory adjust-by-delta")
    c.post("/admin/inventory/add", json={"product_name": "AdjTest", "current_stock": 10, "minimum_stock": 1, "unit": "kg"})
    adj = lambda: next(x for x in c.get("/admin/inventory").json()["data"] if x["product_name"] == "AdjTest")["current_stock"]
    c.post("/admin/inventory/update", json={"product_name": "AdjTest", "delta_quantity": 5, "action_type": "ADJUST_DELTA"})
    check("ADJUST_DELTA +5 -> 15", abs(adj() - 15) < 1e-9, adj())
    c.post("/admin/inventory/update", json={"product_name": "AdjTest", "delta_quantity": -4, "action_type": "ADJUST_DELTA"})
    check("ADJUST_DELTA -4 (remove) -> 11", abs(adj() - 11) < 1e-9, adj())
    r = c.post("/admin/inventory/update", json={"product_name": "AdjTest", "delta_quantity": -999, "action_type": "ADJUST_DELTA"})
    check("ADJUST_DELTA below zero rejected 400", r.status_code == 400, r.text)
    check("ADJUST_DELTA rejection left stock unchanged (11)", abs(adj() - 11) < 1e-9, adj())

print("\n" + "=" * 64)
print(f"RESULT: {_ok} passed, {_fail} failed")
try: os.unlink(_tmp.name)
except OSError: pass
sys.exit(1 if _fail else 0)
