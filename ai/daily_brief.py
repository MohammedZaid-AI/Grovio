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
        # order_history table
        # (id, product, quantity, amount, order_id, created_at)

        amount = float(order[3])

        total_spend += amount

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

    if completed_orders > 0:

        average_order = (
            total_spend /
            completed_orders
        )

    print()
    print("=" * 55)
    print("               GROVIO DAILY BRIEF")
    print("=" * 55)
    print()

    print(
        f"Date                 : "
        f"{datetime.now().strftime('%d %B %Y')}"
    )

    print(
        f"Day                  : "
        f"{today}"
    )

    print()

    print("-" * 55)

    print()

    print(
        f"Pending Orders       : "
        f"{len(pending_orders)}"
    )

    print(
        f"Recurring Orders     : "
        f"{len(orders)}"
    )

    print(
        f"Today's Recurring    : "
        f"{len(today_recurring)}"
    )

    print(
        f"Completed Orders     : "
        f"{completed_orders}"
    )

    print(
        f"Restaurant Spend     : "
        f"₹{total_spend:.2f}"
    )

    print(
        f"Average Order Value  : "
        f"₹{average_order:.2f}"
    )

    print()

    print("-" * 55)

    print()

    print("AI INSIGHTS")
    print()

    if len(pending_orders) > 0:

        print(
            f"✓ {len(pending_orders)} order(s) waiting for approval."
        )

    else:

        print(
            "✓ No pending approvals."
        )

    if len(today_recurring) > 0:

        print(
            f"✓ {len(today_recurring)} recurring order(s) scheduled today."
        )

    else:

        print(
            "✓ No recurring orders scheduled today."
        )

    if completed_orders > 0:

        print(
            f"✓ Total procurement spend is ₹{total_spend:.2f}."
        )

        print(
            f"✓ Average order value is ₹{average_order:.2f}."
        )

    else:

        print(
            "✓ No completed orders yet."
        )

    if total_spend > 5000:

        print(
            "✓ Spending is relatively high. Consider reviewing procurement."
        )

    print()

    print("=" * 55)
    print()