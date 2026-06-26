from db import save_invoice

from ai.invoice.validator import InvoiceValidator
from ai.invoice.inventory_sync import InventorySync


class InvoiceProcessor:

    """
    Complete Invoice Processing Pipeline.

    Validate
        ↓
    Save Invoice
        ↓
    Update Inventory
    """

    def __init__(self):

        self.validator = InvoiceValidator()

        self.inventory = InventorySync()

    def process(self, invoice):

        valid, message = self.validator.validate(invoice)

        if not valid:

            return {

                "success": False,

                "message": message

            }

        # -----------------------------
        # Save invoice
        # -----------------------------

        save_invoice(invoice)

        # -----------------------------
        # Update inventory
        # -----------------------------

        inventory_updates = self.inventory.sync(

            invoice

        )

        return {

            "success": True,

            "message": "Invoice processed successfully.",

            "inventory_updates": inventory_updates

        }


if __name__ == "__main__":

    invoice = {

        "supplier": "ABC Dairy",

        "invoice_number": "INV001",

        "date": "2026-06-26",

        "items": [

            {

                "product": "Milk",

                "quantity": 20,

                "unit": "packets",

                "unit_price": 38,

                "total": 760

            },

            {

                "product": "Butter",

                "quantity": 5,

                "unit": "packs",

                "unit_price": 55,

                "total": 275

            }

        ],

        "total_amount": 1035

    }

    processor = InvoiceProcessor()

    from pprint import pprint

    pprint(

        processor.process(invoice)

    )