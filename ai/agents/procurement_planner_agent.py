import json

from core.llm import llm


PROCUREMENT_PLANNER_PROMPT = """
You are Grovio's AI Procurement Planner.

You are the Chief Procurement Officer of a restaurant.

You will receive the complete restaurant context.

Your job is to decide:

1. What should be purchased today.
2. How much should be purchased.
3. Why.
4. Your confidence.

Consider:

- Inventory
- Demand forecast
- Purchase history
- Weather
- Reservations
- Supplier prices
- Festivals
- Day of week
- Cash flow

Return ONLY JSON.

Example:

{
    "items":[
        {
            "name":"Milk",
            "quantity":3,
            "reason":"Expected breakfast demand."
        },
        {
            "name":"Bread",
            "quantity":5,
            "reason":"Inventory below minimum."
        }
    ],
    "confidence":92,
    "reasoning":[
        "Inventory is below minimum.",
        "Weekend demand is higher."
    ]
}

Never explain.

Never use markdown.

Return JSON only.
"""


class ProcurementPlannerAgent:

    async def execute(

        self,

        context

    ):

        response = llm.chat(

            system=PROCUREMENT_PLANNER_PROMPT,

            user=json.dumps(

                context,

                indent=2

            ),

            temperature=0

        )

        try:

            import re
            json_match = re.search(r"({.*})|(\[.*\])", response, re.DOTALL)
            clean_response = json_match.group(0) if json_match else response
            return json.loads(clean_response)

        except Exception:

            return {

                "items":[],

                "confidence":0,

                "reasoning":[

                    "Unable to generate procurement plan."

                ]

            }


procurement_planner = ProcurementPlannerAgent()