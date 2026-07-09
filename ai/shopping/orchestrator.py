from ai.agents.order_parser_agent import order_parser
from ai.agents.procurement_forecaster import ProcurementForecaster
from ai.intelligence.inventory import Inventory
from ai.intelligence.procurement_planner import ProcurementPlanner
from ai.shopping.shopping_session import shopping_session, _format_candidates
from ai.agents.product_selection_agent import product_selector


class ShoppingOrchestrator:
    """
    Shopping Orchestrator.

    Responsible for:
      - Building shopping lists (manual / forecast / inventory / auto)
      - Driving product search + semantic selection for the current item
      - Resuming a shopping session

    All Swiggy access goes through the ONE persistent SwiggyService owned by the
    ShoppingStateManager for this conversation (shopping_session.get_service),
    so search -> cart -> payment -> checkout share a single MCP session.
    """

    def __init__(self):
        self.forecaster = ProcurementForecaster()
        self.inventory = Inventory()
        self.planner = ProcurementPlanner()

    # ---------------------------------------------------
    # List builders
    # ---------------------------------------------------
    async def manual_order(self, message):
        return await order_parser.execute(message)

    async def forecast_order(self):
        forecast = self.forecaster.execute()
        items = []
        for product in forecast.get("recommended_orders", []):
            items.append({
                "name": product["product"],
                "quantity": product["recommended_quantity"],
            })
        return items

    async def inventory_order(self):
        inventory = self.inventory.execute()
        items = []
        for product in inventory.get("low_stock", []):
            qty = max(1, int(product["minimum"] - product["stock"]))
            items.append({"name": product["product"], "quantity": qty})
        return items

    async def recurring_order(self):
        return []

    async def auto_order(self):
        plan = await self.planner.execute()
        return plan["items"]

    # ---------------------------------------------------
    # Execute / start
    # ---------------------------------------------------
    async def execute(self, source, phone=None, message=None):
        if source == "manual":
            return await self.manual_order(message)
        if source == "forecast":
            return await self.forecast_order()
        if source == "inventory":
            return await self.inventory_order()
        if source == "recurring":
            return await self.recurring_order()
        if source == "auto":
            return await self.auto_order()
        if source == "session":
            return await self.resume_session(phone)
        return []

    async def start(self, phone, source, message=None):
        items = await self.execute(source=source, phone=phone, message=message)

        if source == "session":
            return items

        if not items:
            return {"message": "No products to order."}

        shopping_session.start(phone, items)
        return await self.resume_session(phone)

    # ---------------------------------------------------
    # Resume shopping session — search + semantic selection
    # ---------------------------------------------------
    async def resume_session(self, phone):
        first_item = shopping_session.current_item(phone)

        if not first_item:
            return {"message": "No active shopping session."}

        service = shopping_session.get_service(phone)
        products = await service.search_products(first_item["name"])

        if not products:
            return {"message": f"No products found for {first_item['name']}"}

        # ------------------------------------
        # Semantic ranking (single engine). The user ALWAYS chooses; the ranker
        # is used to order the candidates best-first and to record its top pick
        # so a manual override can be learned.
        # ------------------------------------
        decision = await product_selector.execute(first_item["name"], products)
        category = decision.get("category")

        ordered = self._rank_order(products, decision)

        # Store the ranked candidates; the AI's top pick is now option 1, so the
        # suggested index is 0 for override detection.
        shopping_session.set_options(phone, ordered)
        shopping_session.set_selection_context(
            phone, category, 0, ordered[0].get("displayName")
        )

        return {"message": _format_candidates(first_item["name"], ordered)}

    def _rank_order(self, products, decision):
        """Reorder the candidates so the ranker's best pick is first (option 1),
        followed by the rest of the ranking, then any leftovers. Top 5 shown."""
        order = []
        for r in (decision.get("ranked") or []):
            idx = r.get("index") if isinstance(r, dict) else None
            if isinstance(idx, int) and 0 <= idx < len(products) and idx not in order:
                order.append(idx)
        for i in range(len(products)):
            if i not in order:
                order.append(i)
        return [products[i] for i in order][:5]

    # ---------------------------------------------------
    # Helpers
    # ---------------------------------------------------
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
