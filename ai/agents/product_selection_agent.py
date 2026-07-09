import json
import re

from core.llm import llm
from ai.memory.restaurant_memory import restaurant_memory


# The ONLY component that makes preference decisions. It reasons semantically
# over brand, category, pack size, price, requested quantity, availability,
# popularity and restaurant memory. It understands that "coke" means
# "Coca-Cola" through meaning, NOT through alias tables or string matching.
SEMANTIC_RANKING_PROMPT = """
You are Grovio's Product Selection intelligence — the single decision engine
for choosing which product best fulfils a restaurant's grocery request.

You receive:
1. requested_item        — what the user asked for (name + quantity).
2. restaurant_memory     — learned preferences per category (preferred brand,
                           preferred pack size, preferred supplier, favourites,
                           previously rejected products).
3. candidates            — the products returned by the store, each with an
                           index, name, pack_size, price and availability.

HOW TO DECIDE (reason like a restaurant procurement head):
- Understand the request SEMANTICALLY. "Coke" means Coca-Cola. "Curd" means
  yoghurt. Infer brand, category and intent from meaning — never from a fixed
  alias list and never from raw string overlap.
- Weigh ALL of: semantic match to the request, brand, category, restaurant
  history / preferred brand for this category, preferred pack size, requested
  quantity vs pack size, price, popularity and availability.
- Prefer the restaurant's learned preference for the category WHEN it genuinely
  matches the request, but the semantic meaning of the request always wins over
  a stale preference.
- Ignore unavailable candidates.

DECISION:
- If exactly one candidate is clearly the best fulfilment of the request,
  choose action "auto_select" and give its index.
- If two or more candidates are genuinely, comparably good (a real tie the
  owner would want to resolve), choose action "ask_user".
- Confidence is your own 0-100 certainty that the auto_select is correct.

Return ONLY JSON in this exact shape:

{
  "category": "<inferred category, e.g. cola>",
  "ranked": [
    {"index": 0, "score": 96, "reason": "..."},
    {"index": 2, "score": 60, "reason": "..."}
  ],
  "decision": {
    "action": "auto_select",
    "index": 0,
    "confidence": 96,
    "reason": "..."
  }
}

Never explain outside the JSON. Never use markdown. Return JSON only.
"""


class ProductSelectionAgent:
    """
    Single-engine semantic product selector.

    The LLM is the ONLY component that makes a preference decision. The code
    that follows performs BUSINESS VALIDATION ONLY: it may reject an impossible
    choice (an out-of-range index or an unavailable product) but it must NEVER
    override a valid semantic decision because "another product also looks
    good". That second-guessing rule engine has been removed.
    """

    async def execute(self, query, products):

        candidates = self._normalize(products)

        response = llm.chat(
            system=SEMANTIC_RANKING_PROMPT,
            user=json.dumps(
                {
                    "requested_item": query,
                    "restaurant_memory": restaurant_memory.summary(),
                    "candidates": candidates,
                },
                indent=2,
            ),
            temperature=0,
        )

        decision = self._parse(response)
        decision = self._validate(decision, candidates)

        print(
            f"\n🧠 [Product Selection] '{query}' -> {decision.get('action')} "
            f"(index={decision.get('index')}, category={decision.get('category')}, "
            f"confidence={decision.get('confidence')})\n"
        )

        return decision

    # ------------------------------------------------------------------
    # Candidate retrieval / normalization
    # ------------------------------------------------------------------
    def _normalize(self, products):
        candidates = []
        for i, product in enumerate(products):
            variants = product.get("variations") or [{}]
            variant = variants[0]
            price = None
            try:
                price = variant["price"]["offerPrice"]
            except Exception:
                price = variant.get("price")
            available = product.get("available")
            if available is None:
                available = variant.get("inStock", True)
            candidates.append(
                {
                    "index": i,
                    "name": product.get("displayName"),
                    "pack_size": variant.get("quantityDescription"),
                    "price": price,
                    "available": available,
                }
            )
        return candidates

    # ------------------------------------------------------------------
    # Parse the model's JSON (tolerant of a flat or nested shape)
    # ------------------------------------------------------------------
    def _parse(self, response):
        try:
            match = re.search(r"\{.*\}", response, re.DOTALL)
            data = json.loads(match.group(0) if match else response)
        except Exception:
            return {"action": "ask_user", "confidence": 0,
                    "reason": "Could not parse the ranking response."}

        decision = data.get("decision", data)
        if not isinstance(decision, dict):
            return {"action": "ask_user", "confidence": 0,
                    "reason": "Malformed ranking response."}
        decision.setdefault("category", data.get("category"))
        decision.setdefault("ranked", data.get("ranked"))
        return decision

    # ------------------------------------------------------------------
    # Business validation — reject IMPOSSIBLE choices only
    # ------------------------------------------------------------------
    def _validate(self, decision, candidates):
        action = decision.get("action")

        if action != "auto_select":
            decision["action"] = "ask_user"
            return decision

        index = decision.get("index")

        # Impossible: missing / out-of-range index.
        if not isinstance(index, int) or index < 0 or index >= len(candidates):
            return {
                "action": "ask_user",
                "confidence": decision.get("confidence", 0),
                "reason": "The chosen product index is out of range.",
                "category": decision.get("category"),
            }

        # Impossible: chosen product is unavailable. Fall through to the next
        # available candidate in the model's own ranked order (feasibility, not
        # a preference override); otherwise defer to the user.
        if candidates[index].get("available") is False:
            for r in (decision.get("ranked") or []):
                ri = r.get("index")
                if (isinstance(ri, int) and 0 <= ri < len(candidates)
                        and candidates[ri].get("available") is not False):
                    decision["index"] = ri
                    decision["reason"] = (
                        "Top pick unavailable; selected the next available "
                        "ranked candidate."
                    )
                    return decision
            return {
                "action": "ask_user",
                "confidence": decision.get("confidence", 0),
                "reason": "The preferred product is currently unavailable.",
                "category": decision.get("category"),
            }

        # Valid, feasible auto_select — pass the model's decision through as-is.
        return decision


product_selector = ProductSelectionAgent()
