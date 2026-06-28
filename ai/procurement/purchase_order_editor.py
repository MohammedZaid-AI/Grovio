from db import (
    get_latest_draft_purchase_order,
    get_purchase_order_items,
    update_purchase_order_item,
    delete_purchase_order_item,
    update_purchase_order_total
)


class PurchaseOrderEditor:

    def latest_order(self):

        return get_latest_draft_purchase_order()

    def show(self):

        order = self.latest_order()

        if order is None:

            return None

        items = get_purchase_order_items(order[0])

        return {

            "purchase_order_id": order[0],

            "supplier": order[1],

            "total": order[2],

            "items": items

        }

    def update_quantity(self, product, quantity):

        order = self.latest_order()

        if order is None:

            return False

        purchase_order_id = order[0]

        # Get current items
        items = get_purchase_order_items(
            purchase_order_id
        )

        # Find the product and update it
        for item in items:

            name = item[0]

            price = item[3]

            if name.lower() == product.lower():

                subtotal = price * quantity

                update_purchase_order_item(

                    purchase_order_id,

                    name,      # <-- Use the DB value, not the user's input

                    quantity,

                    subtotal

                )

                break

        # Reload items from DB
        items = get_purchase_order_items(
            purchase_order_id
        )

        total = 0

        for item in items:

            total += item[4]

        update_purchase_order_total(

            purchase_order_id,

            total

        )

        return True

    def remove_product(self, product):

        order = self.latest_order()

        if order is None:

            return False

        purchase_order_id = order[0]

        items = get_purchase_order_items(
            purchase_order_id
        )

        for item in items:

            if item[0].lower() == product.lower():

                delete_purchase_order_item(

                    purchase_order_id,

                    item[0]   # <-- Use exact DB value

                )

                break

        items = get_purchase_order_items(
            purchase_order_id
        )

        total = sum(item[4] for item in items)

        update_purchase_order_total(

            purchase_order_id,

            total

        )

        return True


if __name__ == "__main__":

    editor = PurchaseOrderEditor()

    from pprint import pprint

    pprint(

        editor.show()

    )