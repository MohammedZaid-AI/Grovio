"""Concurrency: a deduction can be approved by exactly ONE of N racing requests,
so stock is never decremented twice (rapid clicks / duplicate requests / 2 tabs)."""
import os, sys, tempfile
from concurrent.futures import ThreadPoolExecutor
sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import db
_tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False); _tmp.close()
db.DB_PATH = _tmp.name; db.init_db()

_ok = _fail = 0
def check(name, cond, extra=""):
    global _ok, _fail
    print(("  ✅ " if cond else "  ❌ ") + name + ("" if cond else f"   {extra}"))
    globals().__setitem__("_ok", _ok + 1) if cond else globals().__setitem__("_fail", _fail + 1)

# Seed: 1 product, 1 recipe, 1 bill -> 1 pending deduction of 0.3 kg.
db.save_inventory("Rice", 50, 10, "kg")
db.save_recipe("Biryani", [{"ingredient_name": "Rice", "quantity_per_unit": 0.15, "unit": "kg"}])
bill_id = db.save_sales_bill("B1", "2026-07-20", 100,
    [{"dish_name": "Biryani", "quantity": 2, "unit_price": 50, "total_price": 100}])
db.confirm_sales_bill(bill_id)

ded = db.get_pending_inventory_deductions()
assert len(ded) == 1, ded
ded_id = ded[0]["id"]
before = db.get_product_inventory("Rice")[2]

# 10 threads race to approve the SAME deduction.
with ThreadPoolExecutor(max_workers=10) as ex:
    results = list(ex.map(lambda _: db.approve_inventory_deduction(ded_id), range(10)))

wins = sum(1 for ok, _ in results if ok)
after = db.get_product_inventory("Rice")[2]

print("\n[Concurrency] 10 racing approvals of one deduction")
check("exactly ONE approval succeeded", wins == 1, f"wins={wins}")
check("stock decremented exactly once (50 - 0.3 = 49.7)", abs(after - (before - 0.3)) < 1e-9, f"after={after}")
check("losers got a clean 'already processed' message",
      all("already" in msg.lower() for ok, msg in results if not ok))

# A subsequent approve is still safely rejected.
ok, msg = db.approve_inventory_deduction(ded_id)
check("post-hoc approve rejected, no further deduction", (not ok) and abs(db.get_product_inventory("Rice")[2] - after) < 1e-9)

print("\n" + "=" * 60)
print(f"RESULT: {_ok} passed, {_fail} failed")
try: os.unlink(_tmp.name)
except OSError: pass
sys.exit(1 if _fail else 0)
