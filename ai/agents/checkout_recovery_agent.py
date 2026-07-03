import json

from core.llm import llm


CHECKOUT_RECOVERY_PROMPT = """
You are Grovio's Checkout Recovery AI.

A grocery checkout has failed.

Your job is to understand the checkout error and decide
what Grovio should do next.

Possible actions:

retry
reduce_quantity
choose_alternative
remove_item
change_store
contact_user

Return ONLY JSON.

Example:

{
    "action":"reduce_quantity",
    "reason":"Only one quantity is available."
}

Example:

{
    "action":"choose_alternative",
    "reason":"Requested product is unavailable."
}

Example:

{
    "action":"retry",
    "reason":"Temporary server issue."
}

Never explain.

Return only JSON.
"""


class CheckoutRecoveryAgent:

    async def execute(

        self,

        error_message

    ):

        response = llm.chat(

            system=CHECKOUT_RECOVERY_PROMPT,

            user=error_message,

            temperature=0

        )

        try:

            import re
            json_match = re.search(r"({.*})|(\[.*\])", response, re.DOTALL)
            clean_response = json_match.group(0) if json_match else response
            return json.loads(clean_response)

        except Exception:

            return {

                "action":"contact_user",

                "reason":"Unable to understand checkout error."

            }


checkout_recovery = CheckoutRecoveryAgent()