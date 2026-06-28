from ai.procurement.purchase_order_approval import PurchaseOrderApproval


class PurchaseApprovalAgent:

    """
    Approves the latest purchase order.
    """

    def __init__(self):

        self.approval = PurchaseOrderApproval()

    def execute(self):

        result = self.approval.approve_latest()

        return {

            "approval": result,

            "message": result["message"]

        }