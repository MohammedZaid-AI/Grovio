from pprint import pprint

from ai.procurement.purchase_order import (
    PurchaseOrder,
    PurchaseOrderItem
)

from ai.intelligence.inventory import InventoryIntelligence
from ai.agents.procurement_forecaster import ProcurementForecaster
from ai.intelligence.supplier_memory import SupplierMemory
from ai.intelligence.price_tracker import PriceTracker


class PurchaseOrderGenerator:
    """
    Generates a smart purchase order using:

    • Inventory
    • Forecast
    • Supplier Memory
    • Price History
    """

    def __init__(self):

        self.inventory = InventoryIntelligence()

        self.forecast = ProcurementForecaster()

        self.suppliers = SupplierMemory()

        self.prices = PriceTracker()

    # ----------------------------------------------------
    # Generate Purchase Order
    # ----------------------------------------------------

    def generate(self):

        inventory = self.inventory.execute()

        forecast = self.forecast.execute()

        low_stock = {

            item["product"]: item

            for item in inventory["low_stock"]

        }

        order = PurchaseOrder(

            supplier=""

        )

        recommendations = forecast.get(

            "recommended_orders",

            []

        )

        for recommendation in recommendations:

            product = recommendation["product"]

            quantity = recommendation["recommended_quantity"]

            # ----------------------------------------
            # Increase quantity if already low stock
            # ----------------------------------------

            if product in low_stock:

                quantity += int(

                    low_stock[product]["minimum"]

                )

            # ----------------------------------------
            # Supplier
            # ----------------------------------------

            supplier = self.suppliers.best_supplier(

                product

            )

            supplier_name = supplier.get(

                "name",

                "Unknown Supplier"

            )

            # ----------------------------------------
            # Latest Price
            # ----------------------------------------

            latest_price = self.prices.latest_price(

                product

            )

            order.supplier = supplier_name

            order.add_item(

                PurchaseOrderItem(

                    product=product,

                    quantity=quantity,

                    unit="units",

                    supplier=supplier_name,

                    estimated_price=latest_price

                )

            )

        return order


if __name__ == "__main__":

    generator = PurchaseOrderGenerator()

    purchase_order = generator.generate()

    pprint(

        purchase_order.to_dict()

    )