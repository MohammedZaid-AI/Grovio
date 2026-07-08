from db import (
    get_inventory,
    get_low_stock_items
)


class Inventory:

    """
    Inventory Intelligence.

    Responsible for analyzing
    current inventory levels.
    """

    def __init__(self):

        self.inventory = get_inventory()

    # -----------------------------------
    # Current Inventory
    # -----------------------------------

    def inventory_summary(self):

        items = []

        for row in self.inventory:

            stock = row[2]
            minimum = row[3]

            items.append({

                "product": row[1],

                "stock": stock,

                "minimum": minimum,

                "unit": row[4],

                "status":

                    "UNKNOWN"

                    if minimum is None

                    else (

                        "LOW"

                        if stock <= minimum

                        else "HEALTHY"

                    )

            })

        return items

    # -----------------------------------
    # Low Stock
    # -----------------------------------

    def low_stock(self):

        items = []

        for row in get_low_stock_items():

            items.append({

                "product": row[1],

                "stock": row[2],

                "minimum": row[3],

                "unit": row[4]

            })

        return items

    # -----------------------------------
    # Inventory Health
    # -----------------------------------

    def health_score(self):

        # Items with no minimum configured are unknowable, not healthy —
        # excluded from both numerator and denominator so missing config
        # can't quietly inflate the score. See security fix H-1/H-2 follow-up.
        trackable = [row for row in self.inventory if row[3] is not None]

        if not trackable:

            return 0

        healthy = sum(1 for row in trackable if row[2] > row[3])

        return round(

            healthy /

            len(trackable) * 100,

            1

        )

    # -----------------------------------
    # Execute
    # -----------------------------------

    def execute(self):

        self.inventory = get_inventory()

        inventory = self.inventory_summary()

        low_stock = self.low_stock()

        health = self.health_score()

        if health >= 90:

            status = "Excellent"

        elif health >= 75:

            status = "Good"

        else:

            status = "Needs Attention"

        return {

            "health_score": health,

            "status": status,

            "inventory": inventory,

            "low_stock": low_stock

        }


if __name__ == "__main__":

    from pprint import pprint

    inventory = Inventory()

    pprint(

        inventory.execute()

    )