import os
import json
import fitz
import easyocr

from PIL import Image
from groq import Groq
from dotenv import load_dotenv

load_dotenv()


class InvoiceParser:

    def __init__(self):

        self.client = Groq(
            api_key=os.getenv("GROQ_API_KEY")
        )

        self.reader = easyocr.Reader(
            ["en"],
            gpu=False
        )

    def extract_text_from_pdf(self, pdf_path):

        document = fitz.open(pdf_path)

        text = ""

        for page in document:

            text += page.get_text()

        document.close()

        return text

    def extract_text_from_image(self, image_path):

        result = self.reader.readtext(image_path)

        text = ""

        for item in result:

            text += item[1] + "\n"

        return text

    def extract_text(self, file_path):

        extension = file_path.lower().split(".")[-1]

        if extension == "pdf":

            return self.extract_text_from_pdf(file_path)

        return self.extract_text_from_image(file_path)

    def parse(self, file_path):

        raw_text = self.extract_text(file_path)

        prompt = f"""
You are an invoice extraction engine.

Extract the invoice into JSON.

Return ONLY valid JSON.

Format:

{{
    "supplier":"",
    "invoice_number":"",
    "date":"",
    "total_amount":0,
    "items":[
        {{
            "product":"",
            "quantity":0,
            "unit":"",
            "unit_price":0,
            "total":0
        }}
    ]
}}

Invoice:

{raw_text}
"""

        response = self.client.chat.completions.create(

            model="openai/gpt-oss-20b",

            temperature=0,

            response_format={
                "type": "json_object"
            },

            messages=[
                {
                    "role": "user",
                    "content": prompt
                }
            ]

        )

        return json.loads(
            response.choices[0].message.content
        )


if __name__ == "__main__":

    parser = InvoiceParser()

    result = parser.parse("sample_invoice.pdf")

    print(json.dumps(result, indent=4))