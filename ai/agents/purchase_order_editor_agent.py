import re

from ai.procurement.purchase_order_editor import PurchaseOrderEditor


class PurchaseOrderEditorAgent:

    def __init__(self):

        self.editor = PurchaseOrderEditor()

    # ----------------------------------

    def execute(self, message):

        message = message.lower().strip()

        # -------------------------------
        # Show Order
        # -------------------------------

        if message in [

            "show",

            "show order",

            "current order",

            "purchase order"

        ]:

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

                f"🏪 Supplier : {order['supplier']}"

            )

            lines.append("")

            for item in order["items"]:

                lines.append(

                    f"• {item[0]} ×{int(item[1])}"

                )

            lines.append("")

            lines.append(

                f"💰 Total : ₹{order['total']:.2f}"

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

                self.editor.update_quantity(

                    product,

                    quantity

                )

                return {

                    "message":

                        f"✅ Updated {product.title()} to {quantity}."

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