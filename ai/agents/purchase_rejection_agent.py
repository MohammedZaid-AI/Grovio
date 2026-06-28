from ai.procurement.purchase_order_rejection import PurchaseOrderRejection


class PurchaseRejectionAgent:
    """
    AI Agent responsible for rejecting
    purchase orders.
    """

    def __init__(self):

        self.rejection = PurchaseOrderRejection()

    def execute(self):

        result = self.rejection.reject()

        if not result["success"]:

            return {

                "rejection": result,

                "message": result["message"]

            }

        message = f"""❌ Purchase Order Cancelled

Purchase Order #{result['purchase_order_id']}

Supplier : {result['supplier']}

Status : REJECTED

No order will be placed.

Reply 'Order groceries' to generate a new purchase order."""

        return {

            "rejection": result,

            "message": message

        }


if __name__ == "__main__":

    agent = PurchaseRejectionAgent()

    print(

        agent.execute()["message"]

    )