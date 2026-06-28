from ai.reports.daily_brief import generate_daily_brief
from ai.intelligence.inventory import Inventory
from ai.agents.procurement_forecaster import ProcurementForecaster
from ai.intelligence.decision_engine import DecisionEngine


class MorningBrief:
    """
    Generates the restaurant's daily
    operational briefing.

    This class only prepares the message.
    It does NOT send WhatsApp messages.
    """

    def __init__(self):

        self.inventory = Inventory()

        self.forecast = ProcurementForecaster()

        self.decision = DecisionEngine()

    # ----------------------------------------
    # Generate Morning Brief
    # ----------------------------------------

    def generate(self):

        brief = generate_daily_brief()

        inventory = self.inventory.execute()

        forecast = self.forecast.execute()

        decision = self.decision.execute()

        lines = []

        lines.append("☀️ Good Morning!")
        lines.append("")
        lines.append("📊 Today's Restaurant Brief")
        lines.append("")

        lines.append(
            f"✅ Completed Orders : {brief['completed_orders']}"
        )

        lines.append(
            f"⏳ Pending Orders : {brief['pending_orders']}"
        )

        lines.append(
            f"💰 Restaurant Spend : ₹{brief['restaurant_spend']}"
        )

        lines.append("")

        # ------------------------------------
        # Inventory Alerts
        # ------------------------------------

        lines.append("📦 Inventory")

        if inventory["low_stock"]:

            for item in inventory["low_stock"]:

                lines.append(

                    f"⚠ {item['product']} : "
                    f"{item['stock']} {item['unit']} remaining"

                )

        else:

            lines.append("✅ Inventory looks healthy.")

        lines.append("")

        # ------------------------------------
        # Procurement
        # ------------------------------------

        lines.append("🛒 Procurement Forecast")

        if forecast["recommended_orders"]:

            for item in forecast["recommended_orders"]:

                lines.append(

                    f"• {item['product']} ×{item['recommended_quantity']}"

                )

        else:

            lines.append(

                "No purchases recommended today."

            )

        lines.append("")

        # ------------------------------------
        # Business Health
        # ------------------------------------

        health = decision["restaurant_health"]

        lines.append(

            f"🧠 Restaurant Health : {health['status']}"

        )

        lines.append("")

        lines.append(

            "Reply 'Order groceries' to generate today's purchase order."

        )

        return "\n".join(lines)


if __name__ == "__main__":

    brief = MorningBrief()

    print()

    print(brief.generate())