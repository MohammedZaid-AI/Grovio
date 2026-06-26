from collections import Counter
from datetime import datetime

from db import (
    get_order_history,
    get_orders,
    get_pending_orders
)


class RestaurantMemory:

    """
    Central memory of the restaurant.

    Every AI module should obtain restaurant
    statistics from here instead of directly
    querying the database.
    """

    def __init__(self):

        self.refresh()

    # ------------------------------------------
    # Refresh Database Cache
    # ------------------------------------------

    def refresh(self):

        self.history = get_order_history()

        self.recurring = get_orders()

        self.pending = get_pending_orders()

    # ------------------------------------------
    # Basic Statistics
    # ------------------------------------------

    def total_completed_orders(self):

        return len(self.history)

    def total_pending_orders(self):

        return len(

            [

                order

                for order in self.pending

                if order[5] == "awaiting_confirmation"

            ]

        )

    def total_spend(self):

        spend = 0.0

        for order in self.history:

            spend += float(order[3])

        return round(spend, 2)

    def average_order_value(self):

        if not self.history:

            return 0

        return round(

            self.total_spend() /

            len(self.history),

            2

        )

    # ------------------------------------------
    # Purchasing Behaviour
    # ------------------------------------------

    def favorite_purchase_day(self):

        counter = Counter()

        for order in self.history:

            date = datetime.strptime(

                order[5],

                "%Y-%m-%d %H:%M:%S"

            )

            counter[

                date.strftime("%A")

            ] += 1

        if not counter:

            return None

        return counter.most_common(1)[0]

    def most_purchased_products(self):

        counter = Counter()

        for order in self.history:

            counter[

                order[1]

            ] += order[2]

        return counter.most_common(10)

    # ------------------------------------------
    # Recurring Orders
    # ------------------------------------------

    def upcoming_recurring_orders(self):

        today = datetime.now().strftime("%A")

        orders = []

        for order in self.recurring:

            recurrence = order[6]

            if not recurrence:

                continue

            days = [

                day.strip()

                for day in recurrence.split(",")

            ]

            if today in days:

                orders.append(

                    {

                        "product":

                            order[1],

                        "quantity":

                            order[3],

                        "time":

                            order[5]

                    }

                )

        return orders

    # ------------------------------------------
    # Complete Context
    # ------------------------------------------

    def execute(self):

        """
        Returns the entire restaurant
        memory in one dictionary.

        LangGraph,
        AI COO,
        WhatsApp,
        APIs

        should use this method.
        """

        return {

            "completed_orders":

                self.total_completed_orders(),

            "pending_orders":

                self.total_pending_orders(),

            "restaurant_spend":

                self.total_spend(),

            "average_order":

                self.average_order_value(),

            "favorite_purchase_day":

                self.favorite_purchase_day(),

            "most_purchased_products":

                self.most_purchased_products(),

            "today_recurring":

                self.upcoming_recurring_orders()

        }


if __name__ == "__main__":

    memory = RestaurantMemory()

    from pprint import pprint

    pprint(

        memory.execute()

    )