from collections import defaultdict

from ai.shopping.orchestrator import ShoppingOrchestrator


class ProcurementEngine:
    """
    Combines every procurement source
    into one shopping list.
    """

    def __init__(self):

        self.orchestrator = ShoppingOrchestrator()

    # ------------------------------------
    # Merge Items
    # ------------------------------------

    def merge(self, *lists):

        merged = defaultdict(int)

        for shopping_list in lists:

            for item in shopping_list:

                merged[item["name"]] += item["quantity"]

        result = []

        for product, quantity in merged.items():

            result.append(

                {

                    "name": product,

                    "quantity": quantity

                }

            )

        return result

    # ------------------------------------
    # Execute
    # ------------------------------------

    async def execute(self):

        forecast = await self.orchestrator.forecast_order()

        inventory = await self.orchestrator.inventory_order()

        recurring = await self.orchestrator.recurring_order()

        return self.merge(

            forecast,

            inventory,

            recurring

        )