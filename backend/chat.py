from ai.reports.daily_brief import generate_daily_brief
from ai.agents.procurement_forecaster import ProcurementForecaster
from ai.intelligence.pattern_detector import PatternDetector
from db import get_order_history, get_pending_orders


def process_message(message: str):

    message = message.lower().strip()

    if message in ["hi", "hello", "hey"]:

        return (
            "🤖 Welcome to Grovio\n\n"
            "Commands:\n"
            "• forecast\n"
            "• patterns\n"
            "• history\n"
            "• pending\n"
            "• daily brief"
        )

    elif "forecast" in message:

        forecasts = ProcurementForecaster().forecast()

        if not forecasts:
            return "No purchase history."

        reply = "📦 Procurement Forecast\n\n"

        for item in forecasts:

            reply += (
                f"• {item['product']}\n"
                f"Probability: {item['probability']}%\n"
                f"Qty: {item['recommended_quantity']}\n\n"
            )

        return reply

    elif "pattern" in message:

        detector = PatternDetector()

        patterns = detector.detect_day_patterns()

        if not patterns:
            return "No patterns found."

        reply = "🧠 Purchase Patterns\n\n"

        for p in patterns:

            reply += (
                f"{p['product']}\n"
                f"Usually: {p['day']}\n"
                f"Confidence: {p['confidence']}%\n\n"
            )

        return reply

    elif "history" in message:

        history = get_order_history()

        if not history:
            return "No order history."

        reply = "📜 Order History\n\n"

        for h in history:

            reply += (
                f"{h[1]}\n"
                f"₹{h[3]}\n"
                f"{h[5]}\n\n"
            )

        return reply

    elif "pending" in message:

        pending = [
            p
            for p in get_pending_orders()
            if p[5] == "awaiting_confirmation"
        ]

        if not pending:
            return "✅ No pending orders."

        reply = "⏳ Pending Orders\n\n"

        for p in pending:

            reply += (
                f"{p[0]}. {p[1]} x{p[3]}\n"
            )

        return reply

    elif "daily" in message:

        return generate_daily_brief()

    return "I didn't understand that."