from db import (
    approve_purchase_order,
    get_latest_purchase_order
)


class PurchaseOrderApproval:

    def approve_latest(self):

        order = get_latest_purchase_order()

        if order is None:

            return {

                "success": False,

                "message": "No purchase orders found."

            }

        approve_purchase_order(

            order[0]

        )

        return {

            "success": True,

            "purchase_order_id": order[0],

            "supplier": order[1],

            "message": "Purchase order approved."

        }


if __name__ == "__main__":

    approval = PurchaseOrderApproval()

    from pprint import pprint

    pprint(

        approval.approve_latest()

    )