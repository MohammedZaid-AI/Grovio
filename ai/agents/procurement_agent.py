from ai.procurement.purchase_order_service import PurchaseOrderService


class ProcurementAgent:
    """
    AI Agent responsible for generating
    smart purchase orders.
    """

    def __init__(self):

        self.service = PurchaseOrderService()

    # --------------------------------------------------
    # Generate Purchase Order
    # --------------------------------------------------

    def execute(self):

        result = self.service.create()

        lines = []

        # ------------------------------------------
        # Header
        # ------------------------------------------

        lines.append(

            f"🛒 Purchase Order #{result['purchase_order_id']}"

        )

        lines.append("")

        lines.append(

            f"🏪 Supplier: {result['supplier']}"

        )

        lines.append("")

        lines.append("📦 Products")

        lines.append("")

        # ------------------------------------------
        # Purchase Order Items
        # ------------------------------------------

        for item in result["items"]:

            lines.append(

                f"• {item['product']}"

            )

            lines.append(

                f"  Qty : {item['quantity']} {item['unit']}"

            )

            lines.append(

                f"  Price : ₹{item['price']:.2f}"

            )

            lines.append(

                f"  Subtotal : ₹{item['subtotal']:.2f}"

            )

            lines.append("")

        # ------------------------------------------
        # Summary
        # ------------------------------------------

        lines.append("────────────────────")

        lines.append(

            f"📦 Total Products : {result['total_items']}"

        )

        lines.append(

            f"📊 Total Quantity : {result['total_quantity']}"

        )

        lines.append(

            f"💰 Estimated Total : ₹{result['total']:.2f}"

        )

        lines.append("")

        lines.append("Reply YES to approve.")

        lines.append("Reply NO to cancel.")

        return {

            "purchase_order": result,

            "message": "\n".join(lines)

        }


if __name__ == "__main__":

    agent = ProcurementAgent()

    result = agent.execute()

    print()

    print(result["message"])