from datetime import datetime

from db import (
    get_orders,
    save_pending_order
)


def run_scheduler():

    today = datetime.now().strftime(
        "%A"
    )

    current_time = datetime.now().strftime(
        "%H:%M"
    )

    print(
        f"Today: {today}"
    )

    print(
        f"Current Time: {current_time}"
    )

    orders = get_orders()

    for order in orders:

        order_id = order[0]

        product_name = order[1]

        spin_id = order[2]

        quantity = order[3]

        order_type = order[4]

        schedule_time = order[5]

        recurrence = order[6]

        status = order[7]

        if order_type != "recurring":
            continue

        if status != "active":
            continue

        if not recurrence:
            continue

        days = [
            d.strip()
            for d in recurrence.split(",")
        ]

        if (
            today in days
            and schedule_time == current_time
        ):

            print(
                "\nRecurring order triggered:"
            )

            print(
                product_name
            )

            save_pending_order(
                product_name,
                spin_id,
                quantity
            )

            print(
                "Saved to pending orders"
            )

if __name__ == "__main__":
    run_scheduler()