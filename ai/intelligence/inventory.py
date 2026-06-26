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

            items.append({

                "product": row[1],

                "stock": row[2],

                "minimum": row[3],

                "unit": row[4],

                "status":

                    "LOW"

                    if row[2] <= row[3]

                    else "HEALTHY"

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

        if not self.inventory:

            return 0

        healthy = 0

        for row in self.inventory:

            if row[2] > row[3]:

                healthy += 1

        return round(

            healthy /

            len(self.inventory) * 100,

            1

        )

    # -----------------------------------
    # Execute
    # -----------------------------------

    def execute(self):

        return {

            "health_score":

                self.health_score(),

            "inventory":

                self.inventory_summary(),

            "low_stock":

                self.low_stock()

        }


if __name__ == "__main__":

    from pprint import pprint

    inventory = Inventory()

    pprint(

        inventory.execute()

    )