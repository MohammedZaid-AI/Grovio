from datetime import datetime

from db import (
    get_orders,
    get_pending_orders,
    get_order_history
)


def generate_daily_brief():
    """
    Returns the restaurant's daily summary
    as a Python dictionary.

    This function contains NO print statements
    so it can be used by:
        - AI COO
        - LangGraph
        - WhatsApp
        - FastAPI
        - CLI
    """

    orders = get_orders()

    pending_orders = get_pending_orders()

    history = get_order_history()

    today = datetime.now().strftime("%A")

    today_recurring = []

    total_spend = 0.0

    for order in history:
        total_spend += float(order[3])

    for order in orders:

        recurrence = order[6]

        if not recurrence:
            continue

        days = [
            day.strip()
            for day in recurrence.split(",")
        ]

        if today in days:
            today_recurring.append(order)

    completed_orders = len(history)

    average_order = 0.0

    if completed_orders:

        average_order = (
            total_spend /
            completed_orders
        )

    insights = []

    if pending_orders:

        insights.append(
            f"{len(pending_orders)} pending approval(s)"
        )

    else:

        insights.append(
            "No pending approvals"
        )

    if today_recurring:

        insights.append(
            f"{len(today_recurring)} recurring order(s) scheduled today"
        )

    else:

        insights.append(
            "No recurring orders scheduled today"
        )

    if total_spend > 5000:

        insights.append(
            "Procurement spend is relatively high."
        )

    if average_order > 500:

        insights.append(
            "Average order value is higher than usual."
        )

    return {

        "date":
            datetime.now().strftime("%d %B %Y"),

        "day":
            today,

        "pending_orders":
            len(pending_orders),

        "recurring_orders":
            len(orders),

        "today_recurring":
            len(today_recurring),

        "completed_orders":
            completed_orders,

        "restaurant_spend":
            round(total_spend, 2),

        "average_order":
            round(average_order, 2),

        "insights":
            insights

    }


def format_daily_brief(data):
    """
    Converts the dictionary returned by
    generate_daily_brief()
    into a human readable report.
    """

    report = []

    report.append("=" * 55)
    report.append("               GROVIO DAILY BRIEF")
    report.append("=" * 55)
    report.append("")

    report.append(
        f"Date                 : {data['date']}"
    )

    report.append(
        f"Day                  : {data['day']}"
    )

    report.append("")
    report.append("-" * 55)
    report.append("")

    report.append(
        f"Pending Orders       : {data['pending_orders']}"
    )

    report.append(
        f"Recurring Orders     : {data['recurring_orders']}"
    )

    report.append(
        f"Today's Recurring    : {data['today_recurring']}"
    )

    report.append(
        f"Completed Orders     : {data['completed_orders']}"
    )

    report.append(
        f"Restaurant Spend     : ₹{data['restaurant_spend']}"
    )

    report.append(
        f"Average Order Value  : ₹{data['average_order']}"
    )

    report.append("")

    report.append("-" * 55)
    report.append("")

    report.append("AI INSIGHTS")
    report.append("")

    for insight in data["insights"]:

        report.append(f"✓ {insight}")

    report.append("")
    report.append("=" * 55)

    return "\n".join(report)


if __name__ == "__main__":

    daily = generate_daily_brief()

    print(

        format_daily_brief(
            daily
        )

    )