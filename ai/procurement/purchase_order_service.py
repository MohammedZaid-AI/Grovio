from db import (
    create_purchase_order,
    add_purchase_order_item
)

from ai.procurement.purchase_order_generator import PurchaseOrderGenerator


class PurchaseOrderService:
    """
    Generates and stores a Purchase Order.
    """

    def __init__(self):

        self.generator = PurchaseOrderGenerator()

    # -----------------------------------------
    # Generate + Save
    # -----------------------------------------

    def create(self):

        purchase_order = self.generator.generate()

        purchase_order_id = create_purchase_order(

            supplier=purchase_order.supplier,

            total_amount=purchase_order.total_amount

        )

        for item in purchase_order.items:

            add_purchase_order_item(

                purchase_order_id,

                item.product,

                item.quantity,

                item.unit,

                item.estimated_price,

                item.subtotal

            )

        return {

            "purchase_order_id": purchase_order_id,

            "supplier": purchase_order.supplier,

            "items": purchase_order.total_items,

            "quantity": purchase_order.total_quantity,

            "total": purchase_order.total_amount

        }


if __name__ == "__main__":

    service = PurchaseOrderService()

    result = service.create()

    from pprint import pprint

    pprint(result)