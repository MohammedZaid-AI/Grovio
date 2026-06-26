from ai.intelligence.memory import RestaurantMemory
from ai.agents.procurement_forecaster import ProcurementForecaster
from ai.intelligence.pattern_detector import PatternDetector
from ai.reports.daily_brief import generate_daily_brief


class ProactiveCOO:

    def __init__(self):

        self.memory = RestaurantMemory()
        self.forecaster = ProcurementForecaster()
        self.patterns = PatternDetector()

    def morning_brief(self):

        return generate_daily_brief()

    def procurement_alerts(self):

        forecasts = self.forecaster.forecast()

        alerts = []

        for item in forecasts:

            if item["probability"] >= 90:

                alerts.append(
                    f"Order {item['product']} today "
                    f"(Confidence {item['probability']}%)"
                )

        return alerts

    def unusual_spending(self):

        total = self.memory.total_spend()

        average = self.memory.average_order()

        alerts = []

        if average == 0:

            return alerts

        if total > average * 10:

            alerts.append(
                "Restaurant spending is unusually high."
            )

        return alerts

    def recurring_reminders(self):

        patterns = self.patterns.detect_day_patterns()

        reminders = []

        for pattern in patterns:

            reminders.append(

                f"{pattern['product']} is usually "
                f"purchased on {pattern['day']}."

            )

        return reminders

    def purchase_predictions(self):

        forecasts = self.forecaster.forecast()

        predictions = []

        for item in forecasts:

            predictions.append(

                {
                    "product": item["product"],
                    "confidence": item["probability"],
                    "quantity": item["recommended_quantity"]
                }

            )

        return predictions

    def risk_analysis(self):

        risks = []

        pending = self.memory.pending_orders()

        if pending > 5:

            risks.append(
                "Too many pending approvals."
            )

        completed = self.memory.completed_orders()

        if completed == 0:

            risks.append(
                "No procurement history available."
            )

        return risks

    def recommendations(self):

        recommendations = []

        forecasts = self.forecaster.forecast()

        for item in forecasts:

            if item["probability"] >= 90:

                recommendations.append(

                    f"Place {item['recommended_quantity']} "
                    f"{item['product']} today."

                )

        recommendations.extend(
            self.unusual_spending()
        )

        recommendations.extend(
            self.risk_analysis()
        )

        return recommendations

    def full_report(self):

        return {

            "daily_brief":

                self.morning_brief(),

            "forecast":

                self.purchase_predictions(),

            "alerts":

                self.procurement_alerts(),

            "reminders":

                self.recurring_reminders(),

            "risks":

                self.risk_analysis(),

            "recommendations":

                self.recommendations()

        }


if __name__ == "__main__":

    coo = ProactiveCOO()

    report = coo.full_report()

    print("\n===== GROVIO PROACTIVE COO =====\n")

    print(report["daily_brief"])

    print("\nForecast")

    print(report["forecast"])

    print("\nAlerts")

    for item in report["alerts"]:

        print("-", item)

    print("\nReminders")

    for item in report["reminders"]:

        print("-", item)

    print("\nRisks")

    for item in report["risks"]:

        print("-", item)

    print("\nRecommendations")

    for item in report["recommendations"]:

        print("-", item)