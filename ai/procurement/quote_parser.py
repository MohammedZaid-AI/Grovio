import json
import os

from core.llm import LLM
from core.config import Config


class QuoteParser:

    def __init__(self):

        self.llm = LLM()

        self.client = self.llm.client

    def parse(self, text):

        prompt = f"""
You extract supplier quotations.

Return JSON only.

Format

{{
"supplier":"",
"items":[
{{
"product":"",
"price":0
}}
]
}}

Quotation

{text}
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

            .message.content

        )


if __name__=="__main__":

    parser=QuoteParser()

    quote="""
ABC Traders

Milk 58

Bread 29

Paneer 312

Butter 58
"""

    print(

        json.dumps(

            parser.parse(
                quote
            ),

            indent=4

        )

    )