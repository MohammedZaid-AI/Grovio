from ai.agents.order_parser_agent import order_parser
from ai.agents.procurement_forecaster import ProcurementForecaster
from ai.intelligence.inventory import Inventory
from ai.intelligence.procurement_planner import ProcurementPlanner
from ai.shopping.shopping_session import shopping_session
from ai.services.swiggy_service import SwiggyService
from ai.agents.product_selection_agent import product_selector

class ShoppingOrchestrator:
    """
    Shopping Orchestrator.

    Responsible for:

    - Building shopping lists
    - Starting shopping sessions
    - Resuming shopping sessions
    - Product search
    - Auto procurement
    """

    def __init__(self):

        self.forecaster = ProcurementForecaster()

        self.inventory = Inventory()

        self.planner = ProcurementPlanner()

        self.service = SwiggyService()

    # ---------------------------------------------------
    # Manual Order
    # ---------------------------------------------------

    async def manual_order(

        self,

        message

    ):

        items = await order_parser.execute(

            message

        )

        return items

    # ---------------------------------------------------
    # Forecast Order
    # ---------------------------------------------------

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

    # ---------------------------------------------------
    # Inventory Order
    # ---------------------------------------------------

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

                    product["minimum"]

                    -

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

    # ---------------------------------------------------
    # Recurring Order
    # ---------------------------------------------------

    async def recurring_order(self):

        # Sprint 3

        return []

    # ---------------------------------------------------
    # Auto Order
    # ---------------------------------------------------

    async def auto_order(self):

        plan = await self.planner.execute()

        return plan["items"]

    # ---------------------------------------------------
    # Execute
    # ---------------------------------------------------

    async def execute(

        self,

        source,

        phone=None,

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

        if source == "session":

            return await self.resume_session(

                phone

            )

        return []

    # ---------------------------------------------------
    # Start Shopping
    # ---------------------------------------------------

    async def start(

        self,

        phone,

        source,

        message=None

    ):

        items = await self.execute(

            source=source,

            phone=phone,

            message=message

        )

        # session source already returns reply

        if source == "session":

            return items

        if not items:

            return {

                "message":

                    "No products to order."

            }

        shopping_session.start(

            phone,

            items

        )

        return await self.resume_session(

            phone

        )

    # ---------------------------------------------------
    # Resume Shopping Session
    # ---------------------------------------------------

    async def resume_session(

        self,

        phone

    ):

        first_item = shopping_session.current_item(

            phone

        )

        if not first_item:

            return {

                "message":

                    "No active shopping session."

            }

        products = await self.service.search_products(

            first_item["name"]

        )

        # ------------------------------------
        # AI Product Selection
        # ------------------------------------

        decision = await product_selector.execute(

            first_item["name"],

            products

        )

        if decision.get("action") == "auto_select":

            shopping_session.set_options(

                phone,

                products

            )

            index = decision["index"]

            shopping_session.select(

                phone,

                index

            )

            print()

            print("=" * 60)

            print("AI Product Selection")

            print(decision)

            print("=" * 60)

            print()

            if shopping_session.finished(phone):

                return {

                    "message": "AUTO_FINISHED"

                }

            return await self.resume_session(

                phone
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

    # ---------------------------------------------------
    # Merge Items
    # ---------------------------------------------------

    def merge_items(

        self,

        *lists

    ):

        merged = {}

        for shopping_list in lists:

            for item in shopping_list:

                name = item["name"]

                if name not in merged:

                    merged[name] = item.copy()

                else:

                    merged[name]["quantity"] += item["quantity"]

        return list(

            merged.values()

        )

    # ---------------------------------------------------
    # Optimize Items
    # ---------------------------------------------------

    def optimize_items(

        self,

        items

    ):

        for item in items:

            if item["quantity"] > 10:

                item["quantity"] = 10

        return items