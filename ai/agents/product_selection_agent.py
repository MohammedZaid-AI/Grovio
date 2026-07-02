import json
from ai.memory.restaurant_memory import restaurant_memory
from core.llm import llm


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

            return json.loads(response)

        except Exception:

            return {

                "action": "ask_user",

                "confidence": 0

            }


product_selector = ProductSelectionAgent()