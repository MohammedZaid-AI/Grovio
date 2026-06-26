class InvoiceValidator:
    """
    Validates invoice data before processing.
    """

    REQUIRED_FIELDS = [

        "supplier",
        "invoice_number",
        "date",
        "items",
        "total_amount"

    ]

    def validate(self, invoice):

        # -------------------------
        # Required fields
        # -------------------------

        for field in self.REQUIRED_FIELDS:

            if field not in invoice:

                return False, f"Missing field: {field}"

        # -------------------------
        # Items
        # -------------------------

        if len(invoice["items"]) == 0:

            return False, "Invoice contains no items."

        calculated_total = 0

        for item in invoice["items"]:

            required = [

                "product",
                "quantity",
                "unit",
                "unit_price",
                "total"

            ]

            for key in required:

                if key not in item:

                    return False, f"Missing '{key}' in item."

            if item["quantity"] <= 0:

                return False, "Quantity must be greater than zero."

            if item["unit_price"] <= 0:

                return False, "Unit price must be greater than zero."

            calculated_total += item["total"]

        # -------------------------
        # Invoice total
        # -------------------------

        if abs(calculated_total - invoice["total_amount"]) > 1:

            return (

                False,

                "Invoice total does not match item totals."

            )

        return True, "Invoice is valid."


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

            }

        ],

        "total_amount": 760

    }

    validator = InvoiceValidator()

    print(

        validator.validate(invoice)

    )