import json
import re

from core.llm import llm
from ai.memory.restaurant_memory import restaurant_memory


# Learning categorizer. It infers each purchased product's semantic CATEGORY,
# BRAND and PACK SIZE so memory can be stored per category. Meaning-based —
# never "first word", never alias tables.
CATEGORIZE_PROMPT = """
You are Grovio's purchase-categorization intelligence.

For each purchased grocery product, infer:
- "category": a short, generic grocery category the restaurant thinks in terms
  of (e.g. "cola", "bread", "ice cream", "milk", "cooking oil"). Use natural
  categories; do NOT invent brand-specific categories.
- "brand": the real manufacturer/brand (e.g. "Coca-Cola", "Amul", "Modern").
  Understand meaning; do NOT just take the first word of the name.
- "pack_size": the pack/quantity descriptor if present (e.g. "750 ml",
  "400 g"), otherwise null.

Return ONLY JSON — a list aligned to the input order:
[
  {"name": "<original name>", "category": "...", "brand": "...", "pack_size": "..."}
]
Return JSON only. No markdown. No explanation.
"""


class MemoryTrainer:
    """
    Learns restaurant preferences from completed purchases.

    Every purchased item is semantically categorized, then reinforced in
    RestaurantMemory by category (brand, pack size, favourite, frequency,
    last-purchase). Because RestaurantMemory derives preferences from these
    counts, future product selections improve automatically.
    """

    def train(self, items):
        if not items:
            return

        names = []
        for it in items:
            name = it.get("displayName") or it.get("name")
            if name:
                names.append(name)
        if not names:
            return

        parsed = self._categorize(names)
        by_name = {p.get("name"): p for p in parsed if isinstance(p, dict)}

        for it in items:
            name = it.get("displayName") or it.get("name")
            if not name:
                continue
            info = by_name.get(name, {})
            restaurant_memory.record_purchase(
                category=info.get("category") or "uncategorized",
                brand=info.get("brand"),
                product=name,
                pack_size=info.get("pack_size") or it.get("pack_size"),
                supplier=it.get("supplier"),
            )

    def _categorize(self, names):
        try:
            response = llm.chat(
                system=CATEGORIZE_PROMPT,
                user=json.dumps(names, indent=2),
                temperature=0,
            )
            match = re.search(r"\[.*\]", response, re.DOTALL)
            data = json.loads(match.group(0) if match else response)
            return data if isinstance(data, list) else []
        except Exception:
            return []


memory_trainer = MemoryTrainer()
