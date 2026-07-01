from ai.agents.procurement_forecaster import ProcurementForecaster
from ai.intelligence.inventory import Inventory


class ProcurementPlanner:
    """
    AI Procurement Planner.

    Responsible for deciding WHAT should be purchased.

    Does NOT:
    - Talk to Swiggy
    - Create carts
    - Checkout

    It only returns a procurement plan.
    """

    def __init__(self):

        self.forecaster = ProcurementForecaster()

        self.inventory = Inventory()

    # --------------------------------------------------
    # Merge Shopping Lists
    # --------------------------------------------------

    def merge_items(self, *lists):

        merged = {}

        for shopping_list in lists:

            for item in shopping_list:

                name = item["name"]

                if name not in merged:

                    merged[name] = item.copy()

                else:

                    merged[name]["quantity"] += item["quantity"]

        return list(merged.values())

    # --------------------------------------------------
    # Quantity Optimizer
    # --------------------------------------------------

    def optimize_items(self, items):

        for item in items:

            # Temporary safety limit
            if item["quantity"] > 10:

                item["quantity"] = 10

        return items

    # --------------------------------------------------
    # Build Procurement Plan
    # --------------------------------------------------

    async def execute(self):

        # -----------------------------
        # Forecast
        # -----------------------------

        forecast_result = self.forecaster.execute()

        forecast_items = []

        for item in forecast_result.get(

            "recommended_orders",

            []

        ):

            forecast_items.append(

                {

                    "name": item["product"],

                    "quantity": item["recommended_quantity"]

                }

            )

        # -----------------------------
        # Inventory
        # -----------------------------

        inventory_result = self.inventory.execute()

        inventory_items = []

        for item in inventory_result.get(

            "low_stock",

            []

        ):

            qty = max(

                1,

                int(

                    item["minimum"]

                    -

                    item["stock"]

                )

            )

            inventory_items.append(

                {

                    "name": item["product"],

                    "quantity": qty

                }

            )

        # -----------------------------
        # Recurring Orders
        # -----------------------------

        recurring_items = []

        # Sprint 3
        # Later we'll fetch recurring
        # supplier orders here.

        # -----------------------------
        # Merge
        # -----------------------------

        items = self.merge_items(

            forecast_items,

            inventory_items,

            recurring_items

        )

        # -----------------------------
        # Optimize
        # -----------------------------

        items = self.optimize_items(

            items

        )

        # -----------------------------
        # Build Reasoning
        # -----------------------------

        reasoning = []

        if forecast_items:

            reasoning.append(

                "Forecast predicts increased demand."

            )

        if inventory_items:

            reasoning.append(

                "Low inventory items require replenishment."

            )

        if recurring_items:

            reasoning.append(

                "Recurring purchases included."

            )

        return {

            "items": items,

            "confidence": 90,

            "reasoning": reasoning

        }