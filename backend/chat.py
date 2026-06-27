from ai.conversation.intent_router import IntentRouter
from ai.conversation.formatter import WhatsAppFormatter

from ai.reports.daily_brief import generate_daily_brief
from ai.intelligence.inventory import InventoryIntelligence
from ai.agents.procurement_forecaster import ProcurementForecaster
from ai.agents.ai_coo import AICOO
from ai.intelligence.decision_engine import DecisionEngine


router = IntentRouter()

formatter = WhatsAppFormatter()


def process_message(message: str):

    intent = router.detect(message)

    print()

    print("=" * 60)

    print("Intent :", intent)

    print("=" * 60)

    print()

    # -----------------------------------
    # Welcome
    # -----------------------------------

    if intent == "menu":

        return formatter.welcome()

    # -----------------------------------
    # Help
    # -----------------------------------

    if intent == "help":

        return formatter.help()

    # -----------------------------------
    # Daily Brief
    # -----------------------------------

    if intent == "daily_brief":

        brief = generate_daily_brief()

        reply = (
            "📊 *Today's Restaurant Brief*\n\n"
            f"Orders : {brief['completed_orders']}\n"
            f"Pending : {brief['pending_orders']}\n"
            f"Spend : ₹{brief['restaurant_spend']}\n"
            f"Average Order : ₹{brief['average_order']}\n\n"
        )

        if brief["insights"]:

            reply += "Insights\n"

            for item in brief["insights"]:

                reply += f"• {item}\n"

        return reply

    # -----------------------------------
    # Inventory
    # -----------------------------------

    if intent == "inventory":

        inventory = InventoryIntelligence().execute()

        reply = (
            "📦 *Inventory Status*\n\n"
            f"Health : {inventory['health_score']}%\n"
            f"Status : {inventory['status']}\n\n"
        )

        if inventory["low_stock"]:

            reply += "⚠️ Low Stock\n"

            for item in inventory["low_stock"]:

                reply += (
                    f"• {item['product']} "
                    f"({item['stock']} {item['unit']})\n"
                )

        else:

            reply += "Everything looks healthy."

        return reply

    # -----------------------------------
    # Forecast
    # -----------------------------------

    if intent == "forecast":

        forecast = ProcurementForecaster().execute()

        reply = (
            "📈 *Procurement Forecast*\n\n"
            f"Confidence : {forecast['confidence']}%\n\n"
        )

        for item in forecast["recommended_orders"]:

            reply += (
                f"• {item['product']}\n"
                f"Qty : {item['recommended_quantity']}\n"
                f"Probability : {item['purchase_probability']}%\n\n"
            )

        return reply

    # -----------------------------------
    # Decision Engine
    # -----------------------------------

    if intent == "decision":

        decision = DecisionEngine().execute()

        reply = (
            "🧠 *Business Decision*\n\n"
            f"Health : {decision['restaurant_health']['status']}\n\n"
        )

        if decision["risks"]:

            reply += "Risks\n"

            for risk in decision["risks"]:

                reply += f"• {risk}\n"

        return reply

    # -----------------------------------
    # Full COO Report
    # -----------------------------------

    if intent == "report":

        report = AICOO().analyze()

        return report["analysis"]

    # -----------------------------------
    # General Chat
    # -----------------------------------

    report = AICOO().analyze()

    return report["analysis"]