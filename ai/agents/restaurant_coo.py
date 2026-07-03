import json

from core.llm import LLM
from core.config import Config

from ai.tool_registry import (
    TOOLS,
    TOOL_DESCRIPTIONS
)


class RestaurantCOO:

    def __init__(self):

        self.llm = LLM()

        self.client = self.llm.client

    def choose_tool(self, message):

        prompt = f"""
You are Grovio.

Your job is ONLY to select a tool.

{TOOL_DESCRIPTIONS}

Return JSON only.

Example

{{
"tool":"forecast"
}}

User:

{message}
"""

        response = self.client.chat.completions.create(

            model=Config.MODEL,

            temperature=0,

            response_format={
                "type":"json_object"
            },

            messages=[

                {
                    "role":"user",
                    "content":prompt
                }

            ]

        )

        return json.loads(

            response
            .choices[0]
            .message
            .content

        )

    def chat(self, message):

        result = self.choose_tool(
            message
        )

        tool = result["tool"]

        print()

        print("Selected Tool")

        print(tool)

        print()

        if tool not in TOOLS:

            return "Unknown tool."

        return TOOLS[tool]()