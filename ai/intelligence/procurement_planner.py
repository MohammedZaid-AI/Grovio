from ai.agents.procurement_forecaster import ProcurementForecaster
from ai.intelligence.inventory import Inventory


class ProcurementPlanner:
    """
    Decides WHAT should be procured.
    Does not talk to Swiggy.
    """

    def __init__(self):

        self.forecaster = ProcurementForecaster()

        self.inventory = Inventory()

    async def execute(self):

        forecast = await self.orchestrator.forecast_order()

        inventory = await self.orchestrator.inventory_order()

        recurring = await self.orchestrator.recurring_order()

        forecast = self.forecaster.execute()

        inventory = self.inventory.execute()
        

        items = self.orchestrator.merge_items(

            forecast,

            inventory,

            recurring

        )

        items = self.orchestrator.optimize_items(items)

        confidence = 90

        reasoning = []

        if forecast:
            reasoning.append(
                "Forecast recommends replenishment."
            )

        if inventory:
            reasoning.append(
                "Some products are below minimum stock."
            )

        if recurring:
            reasoning.append(
                "Recurring purchases included."
            )

        return {

            "items": items,

            "confidence": confidence,

            "reasoning": reasoning

        }
    
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


    def optimize_items(self, items):

        for item in items:

            if item["quantity"] > 10:

                item["quantity"] = 10

        return items