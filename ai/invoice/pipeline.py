from ai.invoice.parser import InvoiceParser
from ai.invoice.extractor import InvoiceExtractor
from ai.invoice.validator import InvoiceValidator
from ai.invoice.processor import InvoiceProcessor


class InvoicePipeline:

    """
    Complete Invoice Processing Pipeline

    Media
        ↓
    Download
        ↓
    Extract Text
        ↓
    AI Extraction
        ↓
    Validation
        ↓
    Database
        ↓
    Inventory
    """

    def __init__(self):

        self.parser = InvoiceParser()

        self.extractor = InvoiceExtractor()

        self.validator = InvoiceValidator()

        self.processor = InvoiceProcessor()

    def process(

        self,

        media_url,

        content_type

    ):

        # -------------------------
        # Download + Extract Text
        # -------------------------

        parsed = self.parser.parse(

            media_url,

            content_type

        )

        # -------------------------
        # AI Extraction
        # -------------------------

        invoice = self.extractor.extract(

            parsed["text"]

        )

        # -------------------------
        # Validation
        # -------------------------

        valid, message = self.validator.validate(

            invoice

        )

        if not valid:

            return {

                "success": False,

                "message": message

            }

        # -------------------------
        # Save everything
        # -------------------------

        result = self.processor.process(

            invoice

        )

        return result


if __name__ == "__main__":

    print()

    print("Invoice Pipeline Ready")

    print()

    print("Waiting for Twilio media...")