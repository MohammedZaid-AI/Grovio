from datetime import datetime
from db import get_orders
from swiggy_mcp import place_order

orders = get_orders()

today = datetime.now().strftime("%A")
current_time = "08:00"

print(f"Today: {today}")
print(f"Current Time: {current_time}")

for order in orders:

    recurrence = order[4]
    schedule_time = order[3]

    if recurrence:

        days = [d.strip() for d in recurrence.split(",")]

        if today in days:

            print(f"Recurring order found: {order}")

            if schedule_time == current_time:
                place_order(order[1], order[2])

    else:

        if schedule_time == current_time:
            place_order(order[1], order[2])