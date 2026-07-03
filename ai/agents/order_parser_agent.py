import json

from core.llm import llm


ORDER_PARSER_PROMPT = """
You are Grovio's Grocery Order Parser.

Your ONLY responsibility is to extract grocery items from the user's message.

Return ONLY valid JSON.

Output format:

[
    {
        "name":"Milk",
        "quantity":2
    }
]

Rules:

- Infer quantity 1 if not specified.
- Extract only grocery or household shopping items.
- Ignore greetings.
- Ignore unrelated conversation.
- Preserve the product name naturally.
- Do not explain.
- Do not use markdown.
- Return [] if there are no grocery items.

Examples

User:
2 Coke
1 Ice Cream

Output

[
    {
        "name":"Coke",
        "quantity":2
    },
    {
        "name":"Ice Cream",
        "quantity":1
    }
]

User:
Need milk butter and bread

Output

[
    {
        "name":"Milk",
        "quantity":1
    },
    {
        "name":"Butter",
        "quantity":1
    },
    {
        "name":"Bread",
        "quantity":1
    }
]

User:
Buy 5 tomatoes and 3 onions

Output

[
    {
        "name":"Tomatoes",
        "quantity":5
    },
    {
        "name":"Onions",
        "quantity":3
    }
]

Return ONLY JSON.
"""


class OrderParserAgent:

    async def execute(

        self,

        message

    ):

        response = llm.chat(

            system=ORDER_PARSER_PROMPT,

            user=message,

            temperature=0

        )

        try:

            import re
            json_match = re.search(r"({.*})|(\[.*\])", response, re.DOTALL)
            clean_response = json_match.group(0) if json_match else response
            return json.loads(clean_response)

        except Exception:

            return []


order_parser = OrderParserAgent()