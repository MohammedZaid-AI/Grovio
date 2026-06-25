from datetime import datetime

from db import (
    get_orders,
    get_pending_orders,
    get_order_history
)


def generate_daily_brief():

    orders = get_orders()

    pending_orders = [
        order
        for order in get_pending_orders()
        if order[5] == "awaiting_confirmation"
    ]

    history = get_order_history()

    today = datetime.now().strftime("%A")

    today_recurring = []

    total_spend = 0.0

    for order in history:
        total_spend += float(order[3])

    for order in orders:

        recurrence = order[6]

        if recurrence:

            days = [
                d.strip()
                for d in recurrence.split(",")
            ]

            if today in days:
                today_recurring.append(order)

    completed_orders = len(history)

    average_order = (
        total_spend / completed_orders
        if completed_orders
        else 0
    )

    report = f"""🤖 *GROVIO DAILY BRIEF*

📅 Date: {datetime.now().strftime('%d %B %Y')}
📆 Day: {today}

📦 Pending Orders: {len(pending_orders)}
🔁 Recurring Orders: {len(orders)}
📌 Today's Recurring: {len(today_recurring)}

✅ Completed Orders: {completed_orders}

💰 Total Spend: ₹{total_spend:.2f}
📊 Average Order: ₹{average_order:.2f}

🧠 AI Insights
"""

    if pending_orders:
        report += f"\n• {len(pending_orders)} pending approval(s)."
    else:
        report += "\n• No pending approvals."

    if today_recurring:
        report += f"\n• {len(today_recurring)} recurring order(s) today."
    else:
        report += "\n• No recurring orders today."

    if completed_orders:
        report += f"\n• Procurement spend: ₹{total_spend:.2f}"

    if total_spend > 5000:
        report += "\n• Spending is unusually high."

    return report