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
You are an expert document extraction AI.
We process two types of documents:
1. SUPPLIER_INVOICE: Billed by suppliers/vendors to the restaurant for raw ingredients (e.g. Milk, Butter, Bread, Oil, Veg).
2. SALES_BILL: Billed by the restaurant to customers for dishes/meals sold (e.g. Chicken Steak, Veg Salad, Butter Toast).

Classify the document type under "doc_type" as either "SUPPLIER_INVOICE" or "SALES_BILL".

Extract ONLY the information available.
Return ONLY valid JSON.

Schema:
{{
    "doc_type": "SUPPLIER_INVOICE" | "SALES_BILL",
    "supplier": "", // For SUPPLIER_INVOICE (e.g. ABC Dairy)
    "invoice_number": "", // invoice number or bill number
    "date": "YYYY-MM-DD",
    "items": [
        {{
            "product": "", // For SUPPLIER_INVOICE, raw ingredient name. For SALES_BILL, prepared dish/item name.
            "quantity": null, // Quantity of item. If missing or unparseable, set to null.
            "unit": "", // unit of measurement (e.g. packets, kg, liters, loaves, unit)
            "unit_price": 0.0,
            "total": 0.0
        }}
    ],
    "total_amount": 0.0
}}

Rules:
- If a quantity is ambiguous or unparseable, set it to null.
- If a field is missing use null or "".
- Return JSON only.

Document Text:
{invoice_text}
"""

        response = llm.chat(
            system="""
You are an expert invoice and sales bill extraction assistant.
Always return valid JSON only.
""",
            user=prompt,
            temperature=0
        )

        try:

            return json.loads(response)

        except Exception:

            return {

                "doc_type": "SUPPLIER_INVOICE",

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