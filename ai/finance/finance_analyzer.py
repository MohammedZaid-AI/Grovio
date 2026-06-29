from db import (
    get_all_purchase_invoices,
    get_invoice_items
)


class FinanceAnalyzer:
    """
    Finance Intelligence for Grovio.

    Analyses procurement invoices and
    purchasing expenditure.
    """

    def __init__(self):

        self.invoices = get_all_purchase_invoices()

    # ----------------------------------
    # Total Spend
    # ----------------------------------

    def total_spend(self):

        total = 0

        for invoice in self.invoices:

            total += float(invoice[4])

        return round(total, 2)

    # ----------------------------------
    # Average Invoice
    # ----------------------------------

    def average_invoice(self):

        if not self.invoices:

            return 0

        return round(

            self.total_spend() /

            len(self.invoices),

            2

        )

    # ----------------------------------
    # Biggest Invoice
    # ----------------------------------

    def biggest_invoice(self):

        if not self.invoices:

            return None

        invoice = max(

            self.invoices,

            key=lambda row: row[4]

        )

        return {

            "supplier": invoice[1],

            "invoice_number": invoice[2],

            "amount": invoice[4],

            "date": invoice[3]

        }

    # ----------------------------------
    # Supplier Spend
    # ----------------------------------

    def supplier_spend(self):

        spend = {}

        for invoice in self.invoices:

            supplier = invoice[1]

            amount = float(invoice[4])

            spend[supplier] = (

                spend.get(

                    supplier,

                    0

                )

                + amount

            )

        return spend

    # ----------------------------------
    # Invoice Count
    # ----------------------------------

    def invoice_count(self):

        return len(

            self.invoices

        )

    # ----------------------------------
    # Execute
    # ----------------------------------

    def execute(self):

        return {

            "invoice_count":

                self.invoice_count(),

            "total_spend":

                self.total_spend(),

            "average_invoice":

                self.average_invoice(),

            "supplier_spend":

                self.supplier_spend(),

            "biggest_invoice":

                self.biggest_invoice()

        }


if __name__ == "__main__":

    from pprint import pprint

    finance = FinanceAnalyzer()

    pprint(

        finance.execute()

    )