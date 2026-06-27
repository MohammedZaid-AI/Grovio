from collections import Counter

from db import (
    get_invoices,
    get_all_products,
    get_price_history,
    get_supplier_statistics
)


class ProcurementMemory:
    """
    Procurement Intelligence Memory.

    Responsible for supplier,
    purchasing and invoice history.
    """

    def __init__(self):

        self.refresh()

    # -----------------------------------
    # Refresh Cache
    # -----------------------------------

    def refresh(self):

        self.invoices = get_invoices()

        self.products = get_all_products()

        self.suppliers = get_supplier_statistics()

    # -----------------------------------
    # Statistics
    # -----------------------------------

    def total_invoices(self):

        return len(self.invoices)

    def total_products(self):

        return len(self.products)

    def total_suppliers(self):

        return len(self.suppliers)

    # -----------------------------------
    # Purchase History
    # -----------------------------------

    def product_history(self, product):

        return get_price_history(product)

    # -----------------------------------
    # Most Purchased Products
    # -----------------------------------

    def most_purchased_products(self):

        counter = Counter()

        for product in self.products:

            history = get_price_history(product)

            counter[product] = len(history)

        return counter.most_common(10)

    # -----------------------------------
    # AI Context
    # -----------------------------------

    def execute(self):

        return {

            "total_invoices":

                self.total_invoices(),

            "total_products":

                self.total_products(),

            "total_suppliers":

                self.total_suppliers(),

            "most_purchased_products":

                self.most_purchased_products()

        }


if __name__ == "__main__":

    from pprint import pprint

    memory = ProcurementMemory()

    pprint(

        memory.execute()

    )