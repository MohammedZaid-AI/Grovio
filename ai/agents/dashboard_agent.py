from ai.intelligence.inventory import Inventory
from ai.finance.finance_analyzer import FinanceAnalyzer
from ai.agents.procurement_forecaster import ProcurementForecaster
from ai.intelligence.decision_engine import DecisionEngine
from ai.intelligence.memory import RestaurantMemory


class DashboardAgent:

    def __init__(self):

        self.inventory = Inventory()

        self.finance = FinanceAnalyzer()

        self.forecast = ProcurementForecaster()

        self.decision = DecisionEngine()

        self.memory = RestaurantMemory()

    def execute(self):

        inventory = self.inventory.execute()

        finance = self.finance.execute()

        forecast = self.forecast.execute()

        decision = self.decision.execute()

        memory = self.memory.execute()

        return {

            "completed_orders": memory["completed_orders"],

            "pending_orders": memory["pending_orders"],

            "inventory_health": inventory["health_score"],

            "inventory_status": (

                "Excellent"

                if inventory["health_score"] >= 90

                else "Good"

                if inventory["health_score"] >= 75

                else "Needs Attention"

            ),

            "total_spend": finance["total_spend"],

            "invoice_count": finance["invoice_count"],

            "average_invoice": finance["average_invoice"],

            "forecast_confidence": forecast["confidence"],

            "restaurant_health": decision["restaurant_health"]["status"],

            "risks": decision["risks"]

        }