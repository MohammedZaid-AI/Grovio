from ai.scheduler.daily_scheduler import DailyScheduler


class WhatsAppScheduler:

    """
    Formats the daily briefing
    into a WhatsApp message.
    """

    def __init__(self):

        self.scheduler = DailyScheduler()

    def generate_message(self):

        data = self.scheduler.execute()

        dashboard = data["dashboard"]

        purchase = data["purchase_order"]["purchase_order"]

        coo = data["coo"]["analysis"]

        reply = []

        reply.append("☀️ *Good Morning!*")

        reply.append("")

        reply.append("━━━━━━━━━━━━━━━━━━")

        reply.append("📊 *Restaurant Dashboard*")

        reply.append("")

        reply.append(

            f"✅ Completed Orders : {dashboard['completed_orders']}"

        )

        reply.append(

            f"⏳ Pending Orders : {dashboard['pending_orders']}"

        )

        reply.append("")

        reply.append(

            f"📦 Inventory : {dashboard['inventory_status']}"

        )

        reply.append(

            f"💰 Spend : ₹{dashboard['total_spend']}"

        )

        reply.append(

            f"🧾 Invoices : {dashboard['invoice_count']}"

        )

        reply.append("")

        reply.append("━━━━━━━━━━━━━━━━━━")

        reply.append("🛒 *Today's Purchase Order*")

        reply.append("")

        reply.append(

            f"Supplier : {purchase['supplier']}"

        )

        reply.append(

            f"Items : {purchase['total_items']}"

        )

        reply.append(

            f"Quantity : {purchase['total_quantity']}"

        )

        reply.append(

            f"Estimated : ₹{purchase['total']}"

        )

        reply.append("")

        reply.append("━━━━━━━━━━━━━━━━━━")

        reply.append("🧠 *AI COO Recommendation*")

        reply.append("")

        reply.append(coo[:400])

        reply.append("")

        reply.append("Reply *YES* to approve today's purchase order.")

        return "\n".join(reply)


if __name__ == "__main__":

    scheduler = WhatsAppScheduler()

    print()

    print(

        scheduler.generate_message()

    )