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

    def execute(self, message=None):

        # Determine if it's an explicit create intent
        is_create_intent = False
        if message:
            msg_lower = message.lower().strip()
            create_phrases = [
                "create order", "create this order", "place this po", "place order",
                "create draft", "draft this", "order this"
            ]
            if any(phrase in msg_lower for phrase in create_phrases):
                is_create_intent = True

        if is_create_intent:
            result = self.service.create()
            is_preview = False
        else:
            result = self.service.generate_preview()
            is_preview = True

        lines = []

        # ------------------------------------------
        # Header
        # ------------------------------------------

        if is_preview:
            lines.append("📋 Procurement Forecast (not yet ordered)")
        else:
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

        already_ordered = getattr(self.service.generator.forecast, "already_ordered", [])
        if already_ordered:
            lines.append("")
            lines.append("Already Ordered (arriving soon):")
            for item in already_ordered:
                lines.append(f"• {item['product']}: already ordered, arriving {item['expected_date']}")

        lines.append("")

        if is_preview:
            lines.append("Reply 'create order' if you'd like me to draft this as a purchase order.")
        else:
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