from collections import Counter
from datetime import datetime

from db import (
    get_order_history,
    get_orders,
    get_pending_orders
)


class RestaurantMemory:

    def __init__(self):

        self.history = get_order_history()

        self.recurring = get_orders()

        self.pending = get_pending_orders()

    def total_completed_orders(self):

        return len(self.history)

    def total_pending_orders(self):

        return len([
            p
            for p in self.pending
            if p[5] == "awaiting_confirmation"
        ])

    def total_spend(self):

        spend = 0

        for order in self.history:

            spend += float(order[3])

        return spend

    def average_order_value(self):

        if not self.history:

            return 0

        return (
            self.total_spend() /
            len(self.history)
        )

    def most_purchased_products(self):

        counter = Counter()

        for order in self.history:

            counter[order[1]] += order[2]

        return counter.most_common(5)

    def favorite_purchase_day(self):

        counter = Counter()

        for order in self.history:

            date = datetime.strptime(
                order[5],
                "%Y-%m-%d %H:%M:%S"
            )

            counter[date.strftime("%A")] += 1

        if not counter:

            return None

        return counter.most_common(1)[0]

    def upcoming_recurring_orders(self):

        today = datetime.now().strftime("%A")

        orders = []

        for order in self.recurring:

            recurrence = order[6]

            if not recurrence:

                continue

            days = [
                d.strip()
                for d in recurrence.split(",")
            ]

            if today in days:

                orders.append(order)

        return orders