from datetime import datetime

from db import (
    get_orders,
    save_pending_order,
    pending_exists
)

def run_scheduler():

    now = datetime.now()

    today = now.strftime(
        "%A"
    )

    current_time = now.strftime(
        "%H:%M"
    )

    print(
        f"\nToday: {today}"
    )

    print(
        f"Current Time: {current_time}"
    )

    orders = get_orders()

    if not orders:

        print(
            "\nNo recurring orders found."
        )

        return

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

        print("\n----------------")

        print(
            f"Checking: {product_name}"
        )

        print(
            f"Days: {days}"
        )

        print(
            f"Schedule Time: {schedule_time}"
        )

        if today not in days:

            print(
                "Skipped (wrong day)"
            )

            continue

        try:

            scheduled = datetime.strptime(
                schedule_time,
                "%H:%M"
            )

        except Exception:

            print(
                f"Invalid time format: {schedule_time}"
            )

            continue

        current_minutes = (
            now.hour * 60
            + now.minute
        )

        scheduled_minutes = (
            scheduled.hour * 60
            + scheduled.minute
        )

        difference = abs(
            current_minutes
            - scheduled_minutes
        )

        print(
            f"Difference: {difference} minute(s)"
        )

        # Trigger if within 5 minutes

        if difference <= 5:

            print(
                "\nRecurring order triggered:"
            )

            print(
                product_name
            )

            if pending_exists(
                product_name,
                spin_id
            ):

                print(
                    "Pending order already exists"
                )

                continue

            save_pending_order(
                product_name,
                spin_id,
                quantity
            )

            print(
                "Saved to pending orders"
            )

        else:

            print(
                "Skipped (time not matched)"
            )


if __name__ == "__main__":

    run_scheduler()