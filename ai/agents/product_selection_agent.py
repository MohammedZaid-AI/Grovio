import json
from ai.memory.restaurant_memory import restaurant_memory
from core.llm import llm
from core.config import Config


PRODUCT_SELECTION_PROMPT = """
You are Grovio's Product Selection AI.

The user requested one grocery item.

You will receive:

1. Restaurant memory
2. User's requested item
3. Swiggy search results

Restaurant memory contains:

- Preferred brands
- Frequently purchased products
- Purchase frequency
- Supplier preferences

Use the restaurant memory whenever possible.

If the preferred brand exists in the product list,
prefer that product.

Only ask the user when multiple equally good choices exist.

Your job is to determine whether one product is clearly the best match.

If one product is clearly the intended product,
auto select it.

If multiple products look equally suitable,
ask the user.

Return ONLY JSON.

Output format:

{
    "action":"auto_select",
    "index":0,
    "confidence":98,
    "reason":"..."
}

or

{
    "action":"ask_user",
    "confidence":65,
    "reason":"Multiple good matches."
}

Never explain.

Never use markdown.

Return JSON only.
"""


class ProductSelectionAgent:

    def extract_brand(self, product_name):
        import re
        tokens = product_name.split()
        brand = "Unknown"
        if tokens:
            for token in tokens:
                if re.search(r"\d", token):
                    continue
                if token.lower() in [
                    "kg", "g", "ml", "l", "ltr", "ltrs", "litre", "litres", 
                    "pc", "pcs", "pack", "packs", "no", "no.", "size", "wt", "vol"
                ]:
                    continue
                brand = token
                break
            if brand == "Unknown" and tokens:
                brand = tokens[0]
        return brand

    async def execute(

        self,

        query,

        products

    ):

        formatted = []

        for i, product in enumerate(products):

            variant = product["variations"][0]

            formatted.append(

                {

                    "index": i,

                    "name": product["displayName"],

                    "quantity": variant["quantityDescription"],

                    "price": variant["price"]["offerPrice"]

                }

            )

        memory = restaurant_memory.get()

        response = llm.chat(

            system=PRODUCT_SELECTION_PROMPT,

            user=json.dumps(

                {

                    "restaurant_memory": memory,

                    "requested_item": query,

                    "products": formatted

                },

                indent=2

            ),

            temperature=0

        )

        try:

            import re
            json_match = re.search(r"({.*})|(\[.*\])", response, re.DOTALL)
            clean_response = json_match.group(0) if json_match else response
            decision = json.loads(clean_response)

        except Exception:

            decision = {

                "action": "ask_user",

                "confidence": 0

            }

        # Apply conservative post-processing validation
        original_decision = decision.copy()
        threshold = getattr(Config, "AUTO_SELECT_CONFIDENCE_THRESHOLD", 98)

        if decision.get("action") == "auto_select":
            selected_index = decision.get("index", 0)
            confidence = decision.get("confidence", 0)

            if selected_index < 0 or selected_index >= len(products):
                decision = {
                    "action": "ask_user",
                    "confidence": 0,
                    "reason": "AI selected index out of range."
                }
            elif confidence < threshold:
                decision = {
                    "action": "ask_user",
                    "confidence": confidence,
                    "reason": f"Confidence {confidence} is below the threshold of {threshold}."
                }
            elif len(products) > 1:
                # Multiple candidates: check for dominant historical preference
                preferred_brands = list(memory.get("preferred_brands", {}).values())
                favorite_products = memory.get("favorite_products", {})

                selected_product = products[selected_index]
                selected_brand = self.extract_brand(selected_product["displayName"])

                has_brand_pref = selected_brand in preferred_brands and selected_brand != "Unknown"
                has_favorite_pref = selected_product["displayName"] in favorite_products and favorite_products[selected_product["displayName"]] > 0

                has_saved_preference = has_brand_pref or has_favorite_pref

                # Check if other candidates also have saved preferences
                other_has_pref = False
                for idx, prod in enumerate(products):
                    if idx == selected_index:
                        continue
                    prod_brand = self.extract_brand(prod["displayName"])
                    if (prod_brand in preferred_brands and prod_brand != "Unknown") or (prod["displayName"] in favorite_products and favorite_products[prod["displayName"]] > 0):
                        other_has_pref = True
                        break

                if not has_saved_preference or other_has_pref:
                    decision = {
                        "action": "ask_user",
                        "confidence": confidence,
                        "reason": f"Multiple candidates exist without a dominant preference (has_saved_preference={has_saved_preference}, other_has_pref={other_has_pref})."
                    }

        # Print before/after decision log for user review
        print(f"\n🧠 [Product Selection] Query: '{query}'")
        print(f"🤖 LLM Decision: {original_decision}")
        print(f"🔒 Final Decision (Threshold: {threshold}): {decision}\n")

        return decision


product_selector = ProductSelectionAgent()