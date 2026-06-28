from pprint import pprint

from db import (
    create_purchase_order,
    add_purchase_order_item
)

from ai.procurement.purchase_order_generator import PurchaseOrderGenerator


class PurchaseOrderService:
    """
    Generates a smart purchase order and
    stores it in the database.
    """

    def __init__(self):

        self.generator = PurchaseOrderGenerator()

    # --------------------------------------------------
    # Generate + Save Purchase Order
    # --------------------------------------------------

    def create(self):

        purchase_order = self.generator.generate()

        purchase_order_id = create_purchase_order(

            supplier=purchase_order.supplier,

            total_amount=purchase_order.total_amount

        )

        # --------------------------------------------
        # Save Purchase Order Items
        # --------------------------------------------

        for item in purchase_order.items:

            add_purchase_order_item(

                purchase_order_id,

                item.product,

                item.quantity,

                item.unit,

                item.estimated_price,

                item.subtotal

            )

        # --------------------------------------------
        # Return Complete Purchase Order
        # --------------------------------------------

        return {

            "purchase_order_id": purchase_order_id,

            "supplier": purchase_order.supplier,

            "items": [

                {

                    "product": item.product,

                    "quantity": item.quantity,

                    "unit": item.unit,

                    "price": item.estimated_price,

                    "subtotal": item.subtotal

                }

                for item in purchase_order.items

            ],

            "total_items": purchase_order.total_items,

            "total_quantity": purchase_order.total_quantity,

            "total": purchase_order.total_amount

        }


# --------------------------------------------------
# Testing
# --------------------------------------------------

if __name__ == "__main__":

    service = PurchaseOrderService()

    result = service.create()

    print()

    print("=" * 60)

    print("PURCHASE ORDER")

    print("=" * 60)

    pprint(result)

    print("=" * 60)