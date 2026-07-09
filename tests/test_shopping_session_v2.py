"""
Increment 2 — shopping session architecture tests.

Covers: conversation recovery, session persistence, cart persistence,
returning after delay, override learning, checkout lifecycle, session
expiration, duplicate-checkout prevention.

The Swiggy MCP boundary is mocked (unreachable from CI/sandbox); the state
manager, resume/expiry logic, lifecycle guards and override learning run for
real. Live MCP-session survival is confirmed via scripts/live_swiggy_delay_test.py.
"""
import os
import sys
import time
import asyncio
import tempfile
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

os.environ.setdefault("OPENAI_API_KEY", "test-dummy-key")
os.environ.setdefault("OPENAI_BASE_URL", "https://example.invalid/v1")
os.environ.setdefault("LLM_MODEL", "test-model")

import db
db.DB_PATH = "database/test_shopping_v2.db"
if os.path.exists(db.DB_PATH):
    os.remove(db.DB_PATH)
db.init_db()

from unittest.mock import patch
from ai.shopping.shopping_session import shopping_session, LIFECYCLE
from ai.memory.restaurant_memory import restaurant_memory
from ai.services.swiggy_service import SwiggyService
import backend.chat as chat

# isolate memory
_mem = tempfile.NamedTemporaryFile(suffix=".json", delete=False)
_mem.close()
restaurant_memory.file = Path(_mem.name)
restaurant_memory.save({"categories": {}})

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


def product(name, pack="1 pc", price=10, spin=None):
    return {"displayName": name,
            "variations": [{"quantityDescription": pack,
                            "price": {"offerPrice": price}, "spinId": spin or name}]}


def seed(phone, stage, items, selected=None, options=None, current=0):
    shopping_session.start(phone, items)
    rec = shopping_session.get(phone)
    rec["stage"] = stage
    rec["current"] = current
    if selected is not None:
        rec["selected"] = selected
    if options is not None:
        rec["options"] = options
    return rec


PH = "whatsapp:+915550000000"


print("=" * 78)
print("Session persistence — single source of truth + ONE Swiggy session")
print("=" * 78)

shopping_session.end(PH)
rec = shopping_session.start(PH, [{"name": "Milk", "quantity": 1}])
required = {"phone", "shopping_id", "created_at", "last_activity", "expires_at",
           "stage", "lifecycle", "selected", "options", "cart_id",
           "swiggy_session", "checkout_state", "payment_state", "approval_state"}
check("record holds the full state schema (single source of truth)",
      required.issubset(set(rec.keys())))

s1 = shopping_session.get_service(PH)
s2 = shopping_session.get_service(PH)
check("get_service returns the SAME persistent SwiggyService instance",
      s1 is s2 and isinstance(s1, SwiggyService))


print()
print("=" * 78)
print("Session expiration")
print("=" * 78)

shopping_session.end(PH)
seed(PH, "selecting", [{"name": "Milk", "quantity": 1}])
shopping_session.get(PH)["expires_at"] = time.time() - 1   # already expired
resp = asyncio.run(chat.process_message(PH, "yes"))
check("expired session -> user told it expired",
      "expired" in resp.lower())
check("expired session is cleared",
      not shopping_session.has_session(PH))


print()
print("=" * 78)
print("Conversation recovery — returning after a delay")
print("=" * 78)

shopping_session.end(PH)
opts = [product("Amul Milk 500ml", "500 ml", 30), product("Nandini Milk 500ml", "500 ml", 28)]
seed(PH, "selecting", [{"name": "Milk", "quantity": 1}], selected=[], options=opts, current=0)
# simulate a 20-minute gap (not expired)
r = shopping_session.get(PH)
r["last_activity"] = time.time() - 20 * 60
r["expires_at"] = time.time() + 20 * 60

resp = asyncio.run(chat.process_message(PH, "hi"))
check("returning after delay -> resume prompt (not auto-restart)",
      "welcome back" in resp.lower() and "Milk" in resp and "CONTINUE" in resp)
check("cart preserved during the away period",
      shopping_session.get(PH)["options"] == opts)

resp2 = asyncio.run(chat.process_message(PH, "continue"))
check("CONTINUE re-renders exactly where the user left off (the Milk options)",
      "Choose Milk" in resp2 and "Amul Milk 500ml" in resp2)
check("resume flag cleared after continue",
      not shopping_session.is_awaiting_resume(PH))

# cancel path
shopping_session.end(PH)
seed(PH, "selecting", [{"name": "Milk", "quantity": 1}], options=opts)
rr = shopping_session.get(PH)
rr["last_activity"] = time.time() - 20 * 60
rr["expires_at"] = time.time() + 20 * 60
asyncio.run(chat.process_message(PH, "hi"))          # -> resume prompt
resp3 = asyncio.run(chat.process_message(PH, "cancel"))
check("CANCEL discards the cart",
      "discarded" in resp3.lower() and not shopping_session.has_session(PH))


print()
print("=" * 78)
print("Override learning — user picks a different product than the AI suggested")
print("=" * 78)

restaurant_memory.save({"categories": {}})
shopping_session.end(PH)
opts = [product("Coca-Cola Zero 750ml", "750 ml", 40), product("Pepsi 750ml", "750 ml", 38)]
seed(PH, "selecting", [{"name": "coke", "quantity": 1}], selected=[], options=opts)
# ranker suggested index 0 (Coca-Cola); user will pick index 1 (Pepsi)
shopping_session.set_selection_context(PH, category="cola", suggested_index=0,
                                       suggested_name="Coca-Cola Zero 750ml")
shopping_session.select(PH, 1)
prof = restaurant_memory.category_profile("cola")
check("override recorded: rejected the AI suggestion (Coca-Cola)",
      "Coca-Cola Zero 750ml" in prof["rejected"])
check("override reinforced the chosen product (Pepsi) as a top preference signal",
      prof["favorite_products"] == ["Pepsi 750ml"])
check("override event logged for audit/learning",
      restaurant_memory.get()["categories"]["cola"]["overrides"] ==
      [{"suggested": "Coca-Cola Zero 750ml", "chosen": "Pepsi 750ml"}])

# picking the suggested product must NOT record an override
restaurant_memory.save({"categories": {}})
shopping_session.end(PH)
seed(PH, "selecting", [{"name": "coke", "quantity": 1}], selected=[], options=opts)
shopping_session.set_selection_context(PH, category="cola", suggested_index=0,
                                       suggested_name="Coca-Cola Zero 750ml")
shopping_session.select(PH, 0)
check("choosing the AI's own suggestion records NO override",
      restaurant_memory.category_profile("cola")["rejected"] == [])


print()
print("=" * 78)
print("Checkout lifecycle + duplicate-checkout prevention")
print("=" * 78)

shopping_session.end(PH)
shopping_session.start(PH, [{"name": "Milk", "quantity": 1}])
check("new session starts at lifecycle 'draft'",
      shopping_session.lifecycle(PH) == "draft")
shopping_session.advance_lifecycle(PH, "cart_ready")
shopping_session.advance_lifecycle(PH, "draft")   # must not go backward
check("lifecycle never moves backward",
      shopping_session.lifecycle(PH) == "cart_ready")

shopping_session.advance_lifecycle(PH, "awaiting_confirmation")
check("begin_checkout succeeds the FIRST time",
      shopping_session.begin_checkout(PH) is True)
check("begin_checkout REJECTS a second (duplicate) attempt",
      shopping_session.begin_checkout(PH) is False)
check("lifecycle order is well-defined and complete",
      LIFECYCLE[-1] == "memory_updated" and LIFECYCLE[0] == "draft")


print()
print("=" * 78)
print("Cart persistence — checkout uses persistent session, rebuilds only if expired")
print("=" * 78)

# _checkout_with_revalidation: happy path -> no rebuild
build_calls = {"n": 0}
checkout_seq = []


async def fake_build(self, selected):
    build_calls["n"] += 1
    return {}


async def ok_checkout(self, **kw):
    checkout_seq.append("ok")
    return {"success": True, "order_placed": True, "order_id": "O1", "status": "CONFIRMED", "total": 10}

with patch.object(SwiggyService, "build_cart", fake_build), \
     patch.object(SwiggyService, "checkout", ok_checkout):
    svc = SwiggyService()
    res = asyncio.run(chat._checkout_with_revalidation(svc, [{"spinId": "x", "quantity": 1}], payment_method="Cash"))
check("happy path: checkout succeeds with NO rebuild between messages",
      res.get("order_placed") and build_calls["n"] == 0 and checkout_seq == ["ok"])

# expired path -> rebuild ONCE and retry
build_calls["n"] = 0
seq = {"i": 0}


async def expiring_checkout(self, **kw):
    seq["i"] += 1
    if seq["i"] == 1:
        return {"success": False, "order_placed": False,
                "raw_error": "Cart not found or session expired", "message": "x"}
    return {"success": True, "order_placed": True, "order_id": "O2", "status": "CONFIRMED", "total": 10}

with patch.object(SwiggyService, "build_cart", fake_build), \
     patch.object(SwiggyService, "checkout", expiring_checkout):
    svc = SwiggyService()
    res = asyncio.run(chat._checkout_with_revalidation(svc, [{"spinId": "x", "quantity": 1}], payment_method="Cash"))
check("expired cart: rebuilt exactly once and retried -> success",
      res.get("order_placed") and build_calls["n"] == 1 and seq["i"] == 2)


print()
print("=" * 78)
print("Duplicate-checkout prevention end-to-end (COD confirm)")
print("=" * 78)

shopping_session.end(PH)
seed(PH, "cod_confirm", [{"name": "Milk", "quantity": 1}],
     selected=[{"displayName": "Amul Milk", "spinId": "s", "quantity": 1, "price": 30}], current=1)
shopping_session.advance_lifecycle(PH, "awaiting_confirmation")

placed = {"n": 0}


async def placing_checkout(self, **kw):
    placed["n"] += 1
    return {"success": True, "order_placed": True, "order_id": "O3", "status": "CONFIRMED", "total": 30}

with patch.object(SwiggyService, "build_cart", fake_build), \
     patch.object(SwiggyService, "checkout", placing_checkout), \
     patch("ai.memory.memory_trainer.memory_trainer.train", lambda *a, **k: None):
    resp = asyncio.run(chat.process_message(PH, "yes"))
check("COD confirm 'yes' places the order exactly once",
      "Order Confirmed" in resp and placed["n"] == 1)
check("session ends after a successful order (no lingering state)",
      not shopping_session.has_session(PH))


print()
print("=" * 78)
print("Always-show-options policy — never auto-select; rank + learn override")
print("=" * 78)

from ai.shopping.orchestrator import ShoppingOrchestrator
import ai.agents.product_selection_agent as psa


async def fake_search(self, name):
    return [product("Coca-Cola 1.25L", "1.25 L", 70, "s10"),
            product("Pepsi 750ml", "750 ml", 38, "s1"),
            product("Paper Boat", "250 ml", 30, "s2")]


async def fake_rank(query, products):
    # ranker is confident (auto_select) but policy must STILL ask the user
    return {"action": "auto_select", "index": 0, "confidence": 98, "category": "cola",
            "ranked": [{"index": 0, "score": 98}, {"index": 1, "score": 50}, {"index": 2, "score": 20}]}


restaurant_memory.save({"categories": {}})
shopping_session.end(PH)
shopping_session.start(PH, [{"name": "coke", "quantity": 1}])
with patch.object(SwiggyService, "search_products", fake_search), \
     patch.object(psa.product_selector, "execute", fake_rank):
    resp = asyncio.run(ShoppingOrchestrator().resume_session(PH))

check("presents options even when the ranker said auto_select (never auto-picks)",
      "Choose coke" in resp["message"] and "1. Coca-Cola 1.25L" in resp["message"])
check("option 1 is the ranker's recommendation, marked",
      "⭐ recommended" in resp["message"])
check("ranker's top pick stored as suggested index 0 for override detection",
      shopping_session.get(PH)["current_suggested_index"] == 0)

# user picks option 2 (Pepsi) instead of the recommended Coca-Cola -> override learned
shopping_session.select(PH, 1)
check("override learned when user overrides the recommendation",
      "Coca-Cola 1.25L" in restaurant_memory.category_profile("cola")["rejected"])


print()
print("=" * 78)
print("RESULT: %d passed, %d failed" % (PASS, FAIL))
print("=" * 78)

for p in (db.DB_PATH, _mem.name):
    try:
        os.remove(p)
    except OSError:
        pass

sys.exit(1 if FAIL else 0)
