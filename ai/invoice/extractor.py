import json

from core.llm import llm


class InvoiceExtractor:

    """
    Converts raw invoice text
    into structured JSON.
    """

    def __init__(self):

        pass

    def extract(self, invoice_text):

        prompt = f"""
You are an invoice extraction AI.

Extract ONLY the information available.

Return ONLY valid JSON.

Schema:

{{
    "supplier": "",

    "invoice_number": "",

    "date": "",

    "items":[

        {{

            "product":"",

            "quantity":0,

            "unit":"",

            "unit_price":0,

            "total":0

        }}

    ],

    "total_amount":0

}}

Rules

- Never invent products.
- Never invent prices.
- Never invent quantities.
- If a field is missing use "" or 0.
- Return JSON only.

Invoice:

{invoice_text}
"""

        response = llm.chat(

            system="""
You are an expert invoice extraction assistant.
Always return valid JSON only.
""",

            user=prompt,

            temperature=0

        )

        try:

            return json.loads(response)

        except Exception:

            return {

                "supplier": "",

                "invoice_number": "",

                "date": "",

                "items": [],

                "total_amount": 0,

                "error": response

            }


if __name__ == "__main__":

    sample_invoice = """

ABC Dairy

Invoice No INV-001

26 June 2026

Milk      20 Packets    ₹38    ₹760

Butter    5 Packs       ₹55    ₹275

Total ₹1035

"""

    extractor = InvoiceExtractor()

    result = extractor.extract(

        sample_invoice

    )

    from pprint import pprint

    pprint(result)