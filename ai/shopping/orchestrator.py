import re

from ai.agents.procurement_forecaster import ProcurementForecaster
from ai.intelligence.inventory import Inventory
from ai.shopping.shopping_session import shopping_session
from ai.services.swiggy_service import SwiggyService
from ai.intelligence.procurement_planner import ProcurementPlanner

class ShoppingOrchestrator:
    """
    Shopping Orchestrator.

    Responsible only for deciding
    WHAT should be ordered.

    It never talks to Swiggy.
    It never performs checkout.
    """

    def __init__(self):

        self.forecaster = ProcurementForecaster()

        self.inventory = Inventory()

        self.service = SwiggyService()

        self.planner = ProcurementPlanner()

    # ------------------------------------
    # Manual Order
    # ------------------------------------

    async def manual_order(self, message):

        items = []

        pattern = r"(\d+)\s+(.+)"

        for line in message.splitlines():

            line = line.strip()

            match = re.match(pattern, line)

            if match:

                items.append(

                    {

                        "name": match.group(2),

                        "quantity": int(match.group(1))

                    }

                )

        return items

    # ------------------------------------
    # Forecast Order
    # ------------------------------------

    async def forecast_order(self):

        forecast = self.forecaster.execute()

        items = []

        for product in forecast.get(
            "recommended_orders",
            []
        ):

            items.append(

                {

                    "name": product["product"],

                    "quantity": product["recommended_quantity"]

                }

            )

        return items

    # ------------------------------------
    # Inventory Order
    # ------------------------------------

    async def inventory_order(self):

        inventory = self.inventory.execute()

        items = []

        for product in inventory.get(
            "low_stock",
            []
        ):

            qty = max(

                1,

                int(

                    product["minimum"] -

                    product["stock"]

                )

            )

            items.append(

                {

                    "name": product["product"],

                    "quantity": qty

                }

            )

        return items

    # ------------------------------------
    # Start Shopping
    # ------------------------------------

    async def start(

        self,

        phone,

        source,

        message=None

    ):

        items = await self.execute(

            source,

            message

        )

        if not items:

            return {

                "message":

                    "No products to order."

            }

        shopping_session.start(

            phone,

            items

        )

        first_item = shopping_session.current_item(

            phone

        )

        products = await self.service.search_products(

            first_item["name"]

        )

        if not products:

            return {

                "message":

                    f"No products found for {first_item['name']}"

            }

        shopping_session.set_options(

            phone,

            products

        )

        reply = []

        reply.append(

            f"Choose {first_item['name']}"

        )

        reply.append("")

        for index, product in enumerate(

            products[:5],

            start=1

        ):

            variant = product["variations"][0]

            reply.append(

                f"{index}. "

                f"{product['displayName']} "

                f"({variant['quantityDescription']}) "

                f"₹{variant['price']['offerPrice']}"

            )

        reply.append("")

        reply.append(

            "Reply with 1-5."

        )

        return {

            "message":

                "\n".join(reply)

        }

    # ------------------------------------
    # Recurring Order
    # ------------------------------------

    async def recurring_order(self):

        return []

    # ------------------------------------
    # Execute
    # ------------------------------------

    async def execute(

        self,

        source,

        message=None

    ):

        if source == "manual":

            return await self.manual_order(
                message
            )

        if source == "forecast":

            return await self.forecast_order()

        if source == "inventory":

            return await self.inventory_order()

        if source == "recurring":

            return await self.recurring_order()
        
        if source == "auto":

            return await self.auto_order()

        return []
    
    # ------------------------------------
    # Merge Items
    # ------------------------------------

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


    # ------------------------------------
    # Optimize Items
    # ------------------------------------

    def optimize_items(self, items):

        for item in items:

            if item["quantity"] > 10:

                item["quantity"] = 10

        return items


    # ------------------------------------
    # Auto Order
    # ------------------------------------

    async def auto_order(self):

        plan = await self.planner.execute()

        return plan["items"]