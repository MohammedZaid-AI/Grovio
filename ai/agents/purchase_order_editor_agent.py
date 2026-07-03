import re

from ai.procurement.purchase_order_editor import PurchaseOrderEditor
from db import get_purchase_order_items


class PurchaseOrderEditorAgent:

    def __init__(self):

        self.editor = PurchaseOrderEditor()

    # ----------------------------------

    def execute(self, message):

        message = message.lower().strip()

        # Check if a draft purchase order exists first
        order = self.editor.latest_order()
        if order is None:
            return {
                "message": "❌ I couldn't edit because no active draft purchase order was found. Please ask me to plan procurement first."
            }

        # -------------------------------
        # Show Order
        # -------------------------------

        if message in [

            "show",

            "show order",

            "current order",

            "purchase order"

        ]:

            order_data = self.editor.show()

            if order_data is None:

                return {

                    "message": "No draft purchase order."

                }

            lines = []

            lines.append(

                f"🛒 Purchase Order #{order_data['purchase_order_id']}"

            )

            lines.append("")

            lines.append(

                f"🏪 Supplier : {order_data['supplier']}"

            )

            lines.append("")

            for item in order_data["items"]:

                lines.append(

                    f"• {item[0]} ×{int(item[1])}"

                )

            lines.append("")

            lines.append(

                f"💰 Total : ₹{order_data['total']:.2f}"

            )

            return {

                "message": "\n".join(lines)

            }

        # -------------------------------
        # Remove Product
        # -------------------------------

        match = re.search(

            r"(remove|delete)\s+(.+)",

            message

        )

        if match:

            product = match.group(2).strip()

            # Validate product exists in draft items
            items = get_purchase_order_items(order[0])
            product_found = any(item[0].lower() == product.lower() for item in items)
            if not product_found:
                return {
                    "message": f"❌ Couldn't remove: Product '{product.title()}' is not in the current draft purchase order items list."
                }

            self.editor.remove_product(
                product
            )

            return {

                "message":

                    f"✅ Removed {product.title()}."

            }

        # -------------------------------
        # Update Quantity
        # -------------------------------

        patterns = [

            r"increase (.+?) to (\d+)",

            r"update (.+?) to (\d+)",

            r"set (.+?) to (\d+)",

            r"change (.+?) to (\d+)"

        ]

        for pattern in patterns:

            match = re.search(

                pattern,

                message

            )

            if match:

                product = match.group(1).strip()

                quantity = int(

                    match.group(2)

                )

                # Validate product exists in draft items
                items = get_purchase_order_items(order[0])
                product_found = any(item[0].lower() == product.lower() for item in items)
                if not product_found:
                    return {
                        "message": f"❌ Couldn't update: Product '{product.title()}' is not in the current draft purchase order items list."
                    }

                self.editor.update_quantity(

                    product,

                    quantity

                )

                return {

                    "message":

                        f"✅ Updated {product.title()} to {quantity}."

                }

        return {

            "message": (
                "I couldn't edit the purchase order. Please specify your edit using one of the following formats:\n"
                "• To remove a product: 'Remove [product]'\n"
                "• To change quantity: 'Change [product] to [quantity]'\n"
                "• To view the draft order: 'Show order'"
            )

        }


if __name__ == "__main__":

    agent = PurchaseOrderEditorAgent()

    while True:

        message = input("> ")

        print()

        result = agent.execute(message)

        print("RESULT:")
        print(result)

        print()