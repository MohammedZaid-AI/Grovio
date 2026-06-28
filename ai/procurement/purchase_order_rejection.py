from db import reject_latest_purchase_order


class PurchaseOrderRejection:
    """
    Rejects the latest draft purchase order.
    """

    def reject(self):

        result = reject_latest_purchase_order()

        if result is None:

            return {

                "success": False,

                "message": "No draft purchase order found."

            }

        return {

            "success": True,

            "purchase_order_id": result["purchase_order_id"],

            "supplier": result["supplier"],

            "message": "Purchase order cancelled."

        }


if __name__ == "__main__":

    rejection = PurchaseOrderRejection()

    from pprint import pprint

    pprint(

        rejection.reject()

    )