import json
from pathlib import Path


class RestaurantMemory:
    """
    Long-term restaurant memory.

    Learns customer preferences over time.
    """

    def __init__(self):

        self.file = Path("data/restaurant_memory.json")

        self.file.parent.mkdir(

            parents=True,

            exist_ok=True

        )

        if not self.file.exists():

            self.save(

                {

                    "preferred_brands": {},

                    "purchase_frequency": {},

                    "favorite_products": {},

                    "supplier_preferences": {},

                    "seasonal_patterns": {},

                    "notes": {}

                }

            )

    # ----------------------------------
    # Load
    # ----------------------------------

    def load(self):

        try:

            with open(

                self.file,

                "r",

                encoding="utf-8"

            ) as f:

                content = f.read().strip()

                if not content:

                    default_data = {

                        "preferred_brands": {},

                        "purchase_frequency": {},

                        "favorite_products": {},

                        "supplier_preferences": {},

                        "seasonal_patterns": {},

                        "notes": {}

                    }

                    self.save(default_data)

                    return default_data

                return json.loads(content)

        except Exception:

            default_data = {

                "preferred_brands": {},

                "purchase_frequency": {},

                "favorite_products": {},

                "supplier_preferences": {},

                "seasonal_patterns": {},

                "notes": {}

            }

            self.save(default_data)

            return default_data

    # ----------------------------------
    # Save
    # ----------------------------------

    def save(

        self,

        data

    ):

        with open(

            self.file,

            "w",

            encoding="utf-8"

        ) as f:

            json.dump(

                data,

                f,

                indent=4

            )

    # ----------------------------------
    # Get
    # ----------------------------------

    def get(self):

        return self.load()

    # ----------------------------------
    # Update Preferred Brand
    # ----------------------------------

    def set_preferred_brand(

        self,

        category,

        brand

    ):

        data = self.load()

        data["preferred_brands"][category] = brand

        self.save(data)

    # ----------------------------------
    # Update Supplier
    # ----------------------------------

    def set_supplier(

        self,

        category,

        supplier

    ):

        data = self.load()

        data["supplier_preferences"][category] = supplier

        self.save(data)

    # ----------------------------------
    # Update Purchase Frequency
    # ----------------------------------

    def update_frequency(

        self,

        product

    ):

        data = self.load()

        freq = data["purchase_frequency"]

        freq[product] = freq.get(

            product,

            0

        ) + 1

        self.save(data)

    # ----------------------------------
    # Favorite Product Counter
    # ----------------------------------

    def update_product(

        self,

        product

    ):

        data = self.load()

        favorites = data["favorite_products"]

        favorites[product] = favorites.get(

            product,

            0

        ) + 1

        self.save(data)

    # ----------------------------------
    # Add Note
    # ----------------------------------

    def add_note(

        self,

        key,

        value

    ):

        data = self.load()

        data["notes"][key] = value

        self.save(data)


restaurant_memory = RestaurantMemory()