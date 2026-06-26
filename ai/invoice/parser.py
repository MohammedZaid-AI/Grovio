import os
import uuid
import requests
import fitz
from requests.auth import HTTPBasicAuth
from dotenv import load_dotenv

load_dotenv()


class InvoiceParser:
    """
    Downloads invoices sent through
    Twilio WhatsApp.

    Current Version (V1)

    Twilio Media URL
            ↓
        Download
            ↓
        Local File

    Future Version (V2)

    Local File
            ↓
        OCR
            ↓
        Structured JSON
    """

    def __init__(self):

        self.account_sid = os.getenv("TWILIO_ACCOUNT_SID")

        self.auth_token = os.getenv("TWILIO_AUTH_TOKEN")

        self.download_folder = "downloads"

        os.makedirs(

            self.download_folder,

            exist_ok=True

        )

    # ------------------------------------------
    # Download Media
    # ------------------------------------------

    def download(

        self,

        media_url,

        content_type

    ):

        extension = self.get_extension(

            content_type

        )

        filename = (

            str(uuid.uuid4())

            + extension

        )

        filepath = os.path.join(

            self.download_folder,

            filename

        )

        response = requests.get(

            media_url,

            auth=HTTPBasicAuth(

                self.account_sid,

                self.auth_token

            )

        )

        if response.status_code != 200:

            raise Exception(

                "Unable to download invoice."

            )

        with open(

            filepath,

            "wb"

        ) as file:

            file.write(

                response.content

            )

        return filepath
    


    def extract_text(self, filepath):

        if filepath.lower().endswith(".pdf"):

            document = fitz.open(filepath)

            text = ""

            for page in document:

                text += page.get_text()

            document.close()

            return text

        return ""

    # ------------------------------------------
    # File Extension
    # ------------------------------------------

    def get_extension(

        self,

        content_type

    ):

        mapping = {

            "application/pdf": ".pdf",

            "image/jpeg": ".jpg",

            "image/jpg": ".jpg",

            "image/png": ".png"

        }

        return mapping.get(

            content_type,

            ".bin"

        )

    # ------------------------------------------
    # Parse
    # ------------------------------------------

    def parse(self, media_url, content_type):

        filepath = self.download(

            media_url,

            content_type

        )

        text = self.extract_text(

            filepath

        )

        return {

            "file_path": filepath,

            "content_type": content_type,

            "text": text

        }


if __name__ == "__main__":

    print()

    print("Invoice Parser Ready")

    print()

    print(

        "Waiting for Twilio media..."

    )