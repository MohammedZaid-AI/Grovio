from ai.intelligence.memory import RestaurantMemory
from ai.intelligence.pattern_detector import PatternDetector
from ai.agents.procurement_forecaster import ProcurementForecaster
from ai.reports.daily_brief import generate_daily_brief


class DecisionEngine:

    """
    Business decision engine.

    This class contains deterministic business
    rules.

    The LLM should explain these decisions,
    not make them.
    """

    def __init__(self):

        self.memory = RestaurantMemory()

        self.patterns = PatternDetector()

        self.forecast = ProcurementForecaster()

    # ------------------------------------
    # Restaurant Health
    # ------------------------------------

    def restaurant_health(self):

        spend = self.memory.total_spend()

        completed = self.memory.total_completed_orders()

        pending = self.memory.total_pending_orders()

        score = 100

        reasons = []

        if completed == 0:

            score -= 40

            reasons.append(
                "No completed orders."
            )

        if pending > 5:

            score -= 20

            reasons.append(
                "Too many pending approvals."
            )

        if spend > 5000:

            score -= 10

            reasons.append(
                "High procurement spending."
            )

        if score >= 85:

            status = "Excellent"

        elif score >= 70:

            status = "Good"

        elif score >= 50:

            status = "Average"

        else:

            status = "Needs Attention"

        return {

            "score": score,

            "status": status,

            "reasons": reasons

        }

    # ------------------------------------
    # Forecast Decision
    # ------------------------------------

    def procurement_decision(self):

        forecast = self.forecast.execute()

        confidence = forecast["confidence"]

        if confidence >= 80:

            action = "AUTO_ORDER"

        elif confidence >= 50:

            action = "SUGGEST_ORDER"

        else:

            action = "WAIT_FOR_MORE_DATA"

        return {

            "confidence": confidence,

            "action": action,

            "forecast": forecast

        }

    # ------------------------------------
    # Business Risks
    # ------------------------------------

    def risks(self):

        risks = []

        memory = self.memory.execute()

        if memory["completed_orders"] < 5:

            risks.append(

                "Very little historical purchasing data."

            )

        if memory["pending_orders"] > 0:

            risks.append(

                "Pending approvals require attention."

            )

        if memory["restaurant_spend"] > 5000:

            risks.append(

                "Procurement spending is increasing."

            )

        return risks

    # ------------------------------------
    # Business Opportunities
    # ------------------------------------

    def opportunities(self):

        opportunities = []

        patterns = self.patterns.execute()

        if patterns["weekly_patterns"]:

            opportunities.append(

                "Recurring purchasing behaviour detected."

            )

        if len(patterns["top_products"]) > 0:

            opportunities.append(

                "Demand forecasting can be improved."

            )

        return opportunities

    # ------------------------------------
    # AI Context
    # ------------------------------------

    def execute(self):

        return {

            "daily_brief":

                generate_daily_brief(),

            "restaurant_health":

                self.restaurant_health(),

            "procurement":

                self.procurement_decision(),

            "risks":

                self.risks(),

            "opportunities":

                self.opportunities()

        }


if __name__ == "__main__":

    from pprint import pprint

    engine = DecisionEngine()

    pprint(

        engine.execute()

    )