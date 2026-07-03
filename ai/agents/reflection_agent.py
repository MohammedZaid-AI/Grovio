import json

from core.llm import llm


REFLECTION_PROMPT = """
You are Grovio's Learning AI.

Your job is to analyse restaurant decisions.

You receive:

- Procurement plan
- Purchased products
- Checkout result
- Restaurant memory

Learn ONE useful lesson.

Return ONLY JSON.

Example:

{
    "lesson":"Reduce milk purchases on Mondays.",
    "confidence":92
}

Example:

{
    "lesson":"Amul Butter is consistently available.",
    "confidence":97
}

Never explain.

Return JSON only.
"""


class ReflectionAgent:

    async def execute(

        self,

        procurement_plan,

        purchased_items,

        checkout_result,

        restaurant_memory

    ):

        response = llm.chat(

            system=REFLECTION_PROMPT,

            user=json.dumps(

                {

                    "procurement_plan": procurement_plan,

                    "purchased_items": purchased_items,

                    "checkout_result": checkout_result,

                    "restaurant_memory": restaurant_memory

                },

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

                "lesson":"No lesson generated.",

                "confidence":0

            }


reflection_agent = ReflectionAgent()