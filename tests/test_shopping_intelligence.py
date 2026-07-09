"""
Boundary tests for the redesigned shopping intelligence (Increment 1):

  * RestaurantMemory  — category-based learning + derived preferences
  * ProductSelectionAgent — SINGLE decision engine (no rule override), business
    validation rejects only IMPOSSIBLE choices
  * MemoryTrainer — semantic per-category learning
  * AutoOrderAgent — manual shopping kept separate from replenishment

The LLM boundary is mocked (Groq is not reachable from CI/sandbox). The ranking
*quality* ("coke" -> Coca-Cola) is confirmed live via a driver script.
"""
import os
import sys
import json
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


# Redirect memory to a throwaway file BEFORE anything reads it.
from ai.memory.restaurant_memory import restaurant_memory
_tmp = tempfile.NamedTemporaryFile(suffix=".json", delete=False)
_tmp.close()
restaurant_memory.file = Path(_tmp.name)
restaurant_memory.save({"categories": {}})

import core.llm


def set_llm(fn):
    core.llm.llm.chat = fn


print("=" * 78)
print("RestaurantMemory — category-based learning")
print("=" * 78)

restaurant_memory.save({"categories": {}})
restaurant_memory.record_purchase("cola", brand="Coca-Cola", product="Coca-Cola Zero 750ml", pack_size="750 ml")
restaurant_memory.record_purchase("cola", brand="Coca-Cola", product="Coca-Cola Zero 750ml", pack_size="750 ml")
restaurant_memory.record_purchase("cola", brand="Pepsi", product="Pepsi 750ml", pack_size="750 ml")
restaurant_memory.record_purchase("bread", brand="Modern", product="Modern Wheat Bread", pack_size="400 g")

check("preferred brand derived from frequency (Coca-Cola beats Pepsi 2:1)",
      restaurant_memory.preferred_brand("cola") == "Coca-Cola")
check("categories are isolated (bread preference independent of cola)",
      restaurant_memory.preferred_brand("bread") == "Modern")
prof = restaurant_memory.category_profile("cola")
check("category profile exposes preferred pack size",
      prof["preferred_pack_size"] == "750 ml" and prof["purchase_count"] == 3)
check("summary lists only categories with signal",
      set(restaurant_memory.summary().keys()) == {"cola", "bread"})

# override + rejection learning
restaurant_memory.save({"categories": {}})
restaurant_memory.record_override("juice", suggested="Real Juice", chosen="Tropicana",
                                  chosen_brand="Tropicana", chosen_pack="1 L")
check("override recorded; suggested product marked rejected",
      "Real Juice" in restaurant_memory.category_profile("juice")["rejected"])
check("override reinforces the chosen brand as preferred",
      restaurant_memory.preferred_brand("juice") == "Tropicana")
restaurant_memory.record_rejection("juice", "Paper Boat")
check("explicit rejection recorded",
      "Paper Boat" in restaurant_memory.category_profile("juice")["rejected"])


print()
print("=" * 78)
print("ProductSelectionAgent — ONE decision engine (no heuristic override)")
print("=" * 78)

from ai.agents.product_selection_agent import product_selector


def swiggy_products(specs):
    """specs: list of (name, pack, price, available)"""
    out = []
    for (name, pack, price, avail) in specs:
        out.append({
            "displayName": name,
            "available": avail,
            "variations": [{"quantityDescription": pack, "price": {"offerPrice": price}, "spinId": name}],
        })
    return out


PRODS = swiggy_products([
    ("Coca-Cola Zero 750ml", "750 ml", 40, True),
    ("Paper Boat Aamras", "250 ml", 30, True),
])

# Seed memory so a DIFFERENT brand is "preferred" for cola — the old rule engine
# would have used this to force ask_user. The single engine must NOT.
restaurant_memory.save({"categories": {}})
restaurant_memory.record_purchase("cola", brand="Pepsi", product="Pepsi 750ml", pack_size="750 ml")

set_llm(lambda system, user, temperature=None: json.dumps({
    "category": "cola",
    "ranked": [{"index": 0, "score": 97, "reason": "Coca-Cola is 'coke'"},
               {"index": 1, "score": 20, "reason": "unrelated"}],
    "decision": {"action": "auto_select", "index": 0, "confidence": 97, "reason": "semantic match"},
}))
d = asyncio.run(product_selector.execute("coke", PRODS))
check("confident LLM auto_select is NOT overridden despite a different preferred brand in memory",
      d["action"] == "auto_select" and d["index"] == 0)

# Impossible: out-of-range index -> ask_user (business validation may reject)
set_llm(lambda system, user, temperature=None: json.dumps({
    "category": "cola",
    "decision": {"action": "auto_select", "index": 9, "confidence": 95, "reason": "x"},
}))
d = asyncio.run(product_selector.execute("coke", PRODS))
check("out-of-range index rejected as impossible -> ask_user",
      d["action"] == "ask_user")

# Impossible: top pick unavailable -> fall to next AVAILABLE ranked candidate
PRODS2 = swiggy_products([
    ("Coca-Cola Zero 750ml", "750 ml", 40, False),   # unavailable
    ("Coca-Cola Classic 750ml", "750 ml", 42, True),
])
set_llm(lambda system, user, temperature=None: json.dumps({
    "category": "cola",
    "ranked": [{"index": 0, "score": 98}, {"index": 1, "score": 90}],
    "decision": {"action": "auto_select", "index": 0, "confidence": 98, "reason": "x"},
}))
d = asyncio.run(product_selector.execute("coke", PRODS2))
check("unavailable top pick -> feasibility fallback to next available (index 1)",
      d["action"] == "auto_select" and d["index"] == 1)

# ask_user passes through untouched
set_llm(lambda system, user, temperature=None: json.dumps({
    "category": "cola",
    "decision": {"action": "ask_user", "confidence": 55, "reason": "genuine tie"},
}))
d = asyncio.run(product_selector.execute("coke", PRODS))
check("genuine ask_user decision passes through unchanged",
      d["action"] == "ask_user")


print()
print("=" * 78)
print("MemoryTrainer — semantic per-category learning")
print("=" * 78)

from ai.memory import memory_trainer as mt_mod

restaurant_memory.save({"categories": {}})
set_llm(lambda system, user, temperature=None: json.dumps([
    {"name": "Coca-Cola Zero 750ml", "category": "cola", "brand": "Coca-Cola", "pack_size": "750 ml"},
    {"name": "Amul Gold Mango Duetz", "category": "ice cream", "brand": "Amul", "pack_size": "60 ml"},
]))
mt_mod.memory_trainer.train([
    {"displayName": "Coca-Cola Zero 750ml", "spinId": "a", "quantity": 1, "price": 40},
    {"displayName": "Amul Gold Mango Duetz", "spinId": "b", "quantity": 2, "price": 20},
])
check("trainer stored cola brand by CATEGORY (not first word)",
      restaurant_memory.preferred_brand("cola") == "Coca-Cola")
check("trainer stored ice cream brand under its own category",
      restaurant_memory.preferred_brand("ice cream") == "Amul")
check("trainer captured pack size for cola",
      restaurant_memory.category_profile("cola")["preferred_pack_size"] == "750 ml")


print()
print("=" * 78)
print("AutoOrderAgent — manual shopping separate from replenishment")
print("=" * 78)

from ai.agents.auto_order_agent import AutoOrderAgent
from ai.agents import order_parser_agent
import db as db_mod

# Manual: explicit items -> exactly those, no replenishment merge.
async def fake_parse_manual(message):
    return [{"name": "coke", "quantity": 2}, {"name": "ice cream", "quantity": 1}]

order_parser_agent.order_parser.execute = fake_parse_manual
db_mod.get_low_stock_items = lambda: [(1, "Bread", 2.0, 5.0, "loaves", "", 1)]

agent = AutoOrderAgent()
res = asyncio.run(agent.execute("order groceries\n2 coke\n1 ice cream"))
item_names = [i["name"] for i in res["items"]]
check("manual mode detected",
      res["mode"] == "manual")
check("items are EXACTLY the user's request (coke, ice cream) — Bread NOT merged",
      item_names == ["coke", "ice cream"] and "Bread" not in item_names)
check("low-stock Bread offered as a SUGGESTION only (mentioned, not ordered)",
      "Bread" in res["message"] and "running low" in res["message"])

# Replenishment: no explicit items -> planner runs.
async def fake_parse_empty(message):
    return []

order_parser_agent.order_parser.execute = fake_parse_empty

async def fake_plan():
    return {"items": [{"name": "Bread", "quantity": 3}], "confidence": 80,
            "reasoning": ["Bread below minimum."]}

agent2 = AutoOrderAgent()
agent2.planner.execute = fake_plan
res2 = asyncio.run(agent2.execute("order groceries"))
check("replenishment mode detected when no explicit items",
      res2["mode"] == "replenishment")
check("replenishment items come from the planner (Bread x3)",
      [i["name"] for i in res2["items"]] == ["Bread"])


print()
print("=" * 78)
print("RESULT: %d passed, %d failed" % (PASS, FAIL))
print("=" * 78)

try:
    os.remove(_tmp.name)
except OSError:
    pass

sys.exit(1 if FAIL else 0)
