from ai.procurement.purchase_order_service import PurchaseOrderService
from ai.procurement.purchase_order_approval import PurchaseOrderApproval


class ProcurementTool:

    def __init__(self):

        self.service = PurchaseOrderService()

        self.approval = PurchaseOrderApproval()

    # ----------------------------------
    # Generate Purchase Order
    # ----------------------------------

    def generate_purchase_order(self):

        result = self.service.create()

        message = []

        message.append("🛒 Purchase Order Generated")
        message.append("")
        message.append(f"Supplier : {result['supplier']}")
        message.append("")
        message.append(f"Items : {result['items']}")
        message.append(f"Quantity : {result['quantity']}")
        message.append(f"Estimated Total : ₹{result['total']}")
        message.append("")
        message.append("Reply YES to approve.")

        return "\n".join(message)

    # ----------------------------------
    # Approve Purchase Order
    # ----------------------------------

    def approve_purchase_order(self):

        result = self.approval.approve_latest()

        if not result["success"]:

            return result["message"]

        return (

            "✅ Purchase Order Approved\n\n"

            f"Supplier : {result['supplier']}\n"

            f"Purchase Order : #{result['purchase_order_id']}"
        )