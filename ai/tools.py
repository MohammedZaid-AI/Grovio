from ai.daily_brief import generate_daily_brief
from ai.procurement_forecaster import ProcurementForecaster
from ai.pattern_detector import PatternDetector

from db import (
    get_order_history,
    get_pending_orders
)


def daily_brief():

    return generate_daily_brief()


def forecast():

    forecasts = ProcurementForecaster().forecast()

    if not forecasts:

        return "No purchase history."

    reply = "📦 Procurement Forecast\n\n"

    for item in forecasts:

        reply += (
            f"{item['product']}\n"
            f"Probability : {item['probability']}%\n"
            f"Suggested Qty : {item['recommended_quantity']}\n\n"
        )

    return reply


def pending():

    orders = [

        order

        for order in get_pending_orders()

        if order[5] == "awaiting_confirmation"

    ]

    if not orders:

        return "No pending approvals."

    reply = "Pending Orders\n\n"

    for order in orders:

        reply += (
            f"{order[0]}. "
            f"{order[1]} "
            f"x{order[3]}\n"
        )

    return reply


def history():

    orders = get_order_history()

    if not orders:

        return "No history."

    reply = "Order History\n\n"

    for order in orders:

        reply += (
            f"{order[1]}\n"
            f"₹{order[3]}\n"
            f"{order[5]}\n\n"
        )

    return reply


def patterns():

    detector = PatternDetector()

    patterns = detector.detect_day_patterns()

    if not patterns:

        return "No buying patterns."

    reply = "Buying Patterns\n\n"

    for p in patterns:

        reply += (
            f"{p['product']}\n"
            f"{p['day']}\n"
            f"{p['confidence']}%\n\n"
        )

    return reply