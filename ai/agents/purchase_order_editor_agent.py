import re

from ai.procurement.purchase_order_editor import PurchaseOrderEditor


class PurchaseOrderEditorAgent:

    def __init__(self):

        self.editor = PurchaseOrderEditor()

    # ----------------------------------

    def execute(self, message):

        print("Received:", message)

        message = message.lower().strip()

        # -------------------------------
        # Show Order
        # -------------------------------

        if "show" in message:

            order = self.editor.show()

            if order is None:

                return {

                    "message": "No draft purchase order."

                }

            lines = []

            lines.append(

                f"🛒 Purchase Order #{order['purchase_order_id']}"

            )

            lines.append("")

            lines.append(

                f"Supplier : {order['supplier']}"

            )

            lines.append("")

            for item in order["items"]:

                lines.append(

                    f"• {item[0]} ×{item[1]}"

                )

            lines.append("")

            lines.append(

                f"Total : ₹{order['total']}"

            )

            return {

                "message": "\n".join(lines)

            }

        # -------------------------------
        # Remove Product
        # -------------------------------

        if message.startswith("remove"):

            product = message.replace(

                "remove",

                ""

            ).strip()

            self.editor.remove_product(

                product

            )

            return {

                "message":

                    f"✅ {product} removed."

            }

        # -------------------------------
        # Increase Quantity
        # -------------------------------

        match = re.search(

            r"increase (.+?) to (\d+)",

            message

        )

        if match:

            product = match.group(1)

            quantity = int(

                match.group(2)

            )

            self.editor.update_quantity(

                product,

                quantity

            )

            return {

                "message":

                    f"✅ Updated {product} to {quantity}."

            }

        return {

            "message":

                "I couldn't edit the purchase order."

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