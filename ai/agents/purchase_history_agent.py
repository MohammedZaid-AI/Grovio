from ai.procurement.purchase_order_history import PurchaseOrderHistory


class PurchaseHistoryAgent:

    def __init__(self):

        self.history = PurchaseOrderHistory()

    def execute(self, message):

        message = message.lower().strip()

        # -----------------------------
        # Latest Purchase Order
        # -----------------------------

        if message in {

            "last order",

            "latest order"

        }:

            order = self.history.execute()

            if "message" in order:

                return order

            reply = []

            reply.append(

                f"🛒 Purchase Order #{order['purchase_order_id']}"

            )

            reply.append("")

            reply.append(

                f"Supplier : {order['supplier']}"

            )

            reply.append(

                f"Status : {order['status']}"

            )

            reply.append("")

            for item in order["items"]:

                reply.append(

                    f"• {item[0]} ×{int(item[1])}"

                )

            reply.append("")

            reply.append(

                f"💰 Total : ₹{order['total']:.2f}"

            )

            return {

                "message": "\n".join(reply)

            }

        # -----------------------------
        # Complete History
        # -----------------------------

        orders = self.history.all_orders()

        if not orders:

            return {

                "message":

                    "No purchase history found."

            }

        reply = []

        reply.append(

            "📜 Purchase History"

        )

        reply.append("")

        for order in orders:

            reply.append(

                f"PO #{order[0]}"

            )

            reply.append(

                f"{order[1]}"

            )

            reply.append(

                f"{order[2]}"

            )

            reply.append(

                f"₹{order[3]:.2f}"

            )

            reply.append("")

        return {

            "message":

                "\n".join(reply)

        }