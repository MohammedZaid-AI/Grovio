from db import (
    get_purchase_orders,
    get_purchase_order_items_by_order
)


class PurchaseOrderHistory:

    def all_orders(self):

        return get_purchase_orders()

    def latest_order(self):

        orders = get_purchase_orders()

        if not orders:

            return None

        return orders[0]

    def by_status(self, status):

        orders = []

        for order in get_purchase_orders():

            if order[2].upper() == status.upper():

                orders.append(order)

        return orders

    def details(self, purchase_order_id):

        items = get_purchase_order_items_by_order(

            purchase_order_id

        )

        return items

    def execute(self):

        latest = self.latest_order()

        if latest is None:

            return {

                "message": "No purchase orders found."

            }

        items = self.details(

            latest[0]

        )

        return {

            "purchase_order_id": latest[0],

            "supplier": latest[1],

            "status": latest[2],

            "total": latest[3],

            "created_at": latest[4],

            "items": items

        }


if __name__ == "__main__":

    from pprint import pprint

    history = PurchaseOrderHistory()

    pprint(

        history.execute()

    )