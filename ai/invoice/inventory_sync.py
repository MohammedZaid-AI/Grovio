from db import (
    get_product_inventory,
    save_inventory,
    update_inventory
)


class InventorySync:
    """
    Updates inventory using invoice items.
    """

    def sync(self, invoice):

        updated_items = []

        for item in invoice["items"]:

            product = item["product"]

            quantity = item["quantity"]

            unit = item["unit"]

            existing = get_product_inventory(product)

            if existing is None:

                save_inventory(

                    product_name=product,

                    current_stock=quantity,

                    minimum_stock=5,

                    unit=unit

                )

                new_stock = quantity

            else:

                update_inventory(

                    product,

                    quantity

                )

                updated = get_product_inventory(product)

                new_stock = updated[2]

            updated_items.append(

                {

                    "product": product,

                    "added": quantity,

                    "new_stock": new_stock,

                    "unit": unit

                }

            )

        return updated_items


if __name__ == "__main__":

    invoice = {

        "supplier": "ABC Dairy",

        "invoice_number": "INV-001",

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

    sync = InventorySync()

    from pprint import pprint

    pprint(

        sync.sync(invoice)

    )