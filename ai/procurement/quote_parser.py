import json
import os

from groq import Groq
from dotenv import load_dotenv

load_dotenv()


class QuoteParser:

    def __init__(self):

        self.client = Groq(

            api_key=os.getenv(
                "GROQ_API_KEY"
            )

        )

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

            model="openai/gpt-oss-20b",

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