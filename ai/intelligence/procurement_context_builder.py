from datetime import datetime

from ai.agents.procurement_forecaster import ProcurementForecaster
from ai.intelligence.inventory import Inventory


class ProcurementContextBuilder:

    def __init__(self):

        self.forecaster = ProcurementForecaster()

        self.inventory = Inventory()

    async def build(self):

        forecast = self.forecaster.execute()

        inventory = self.inventory.execute()

        context = {

            "date": datetime.now().strftime("%Y-%m-%d"),

            "day": datetime.now().strftime("%A"),

            "forecast": forecast,

            "inventory": inventory,

            "purchase_history": [],

            "weather": None,

            "reservations": None,

            "supplier_prices": None,

            "festival": None,

            "cash_flow": None

        }

        return context


context_builder = ProcurementContextBuilder()