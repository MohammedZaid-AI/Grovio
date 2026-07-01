import re

from ai.agents.procurement_forecaster import ProcurementForecaster
from ai.intelligence.inventory import Inventory
from ai.shopping.shopping_session import shopping_session
from ai.services.swiggy_service import SwiggyService

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

        return []