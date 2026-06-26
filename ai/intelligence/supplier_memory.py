from db import (
    get_top_suppliers,
    get_supplier_statistics,
    get_supplier_prices,
    get_total_spend_by_supplier,
    get_all_products,
    get_cheapest_supplier
)


class SupplierMemory:

    def get_all_suppliers(self):

        return get_top_suppliers()

    def supplier_summary(self, supplier):

        spend = get_total_spend_by_supplier(
            supplier
        )

        products = []

        for product in get_all_products():

            prices = get_supplier_prices(product)

            for s, price in prices:

                if s == supplier:

                    products.append(

                        {
                            "product": product,
                            "avg_price": price
                        }

                    )

        return {

            "supplier": supplier,

            "total_spend": spend,

            "products": products,

            "product_count": len(products)

        }

    def cheapest_products(self):

        cheapest = []

        for product in get_all_products():

            supplier = get_cheapest_supplier(
                product
            )

            if supplier:

                cheapest.append(

                    {
                        "product": product,

                        "supplier": supplier[0],

                        "price": supplier[1]
                    }

                )

        return cheapest

    def supplier_score(self, supplier):

        summary = self.supplier_summary(
            supplier
        )

        score = 100

        if summary["product_count"] < 5:

            score -= 10

        if summary["total_spend"] < 1000:

            score -= 10

        return score

    def supplier_report(self):

        report = []

        suppliers = self.get_all_suppliers()

        for supplier in suppliers:

            report.append(

                {

                    "supplier": supplier[0],

                    "orders": supplier[1],

                    "score": self.supplier_score(
                        supplier[0]
                    ),

                    "summary":

                        self.supplier_summary(
                            supplier[0]
                        )

                }

            )

        return report


if __name__ == "__main__":

    memory = SupplierMemory()

    print()

    print("=" * 60)

    print("SUPPLIER REPORT")

    print("=" * 60)

    print()

    for supplier in memory.supplier_report():

        print(

            supplier["supplier"]

        )

        print(

            "Orders :",

            supplier["orders"]

        )

        print(

            "Score :",

            supplier["score"]

        )

        print(

            "Spend : ₹",

            supplier["summary"]["total_spend"]

        )

        print()

    print("=" * 60)