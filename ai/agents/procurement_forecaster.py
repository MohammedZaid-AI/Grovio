from collections import Counter
from datetime import datetime, timedelta

from ai.intelligence.memory import RestaurantMemory


class ProcurementForecaster:

    """
    Predicts upcoming procurement needs
    using historical purchasing behaviour.
    """

    def __init__(self):

        self.memory = RestaurantMemory()

    # ---------------------------------------
    # Forecast Engine
    # ---------------------------------------

    def forecast(self):

        history = self.memory.history

        if not history:

            return []

        counter = Counter()

        for order in history:

            product = order[1]

            quantity = order[2]

            counter[product] += quantity

        recommendations = []

        total_orders = len(history)

        for product, quantity in counter.items():

            probability = round(

                (quantity / total_orders) * 100,

                2

            )

            recommendations.append(

                {

                    "product":

                        product,

                    "purchase_probability":

                        probability,

                    "recommended_quantity":

                        max(

                            1,

                            round(

                                quantity /

                                total_orders

                            )

                        ),

                    "reason":

                        f"Purchased {quantity} unit(s) in {total_orders} historical orders."

                }

            )

        recommendations.sort(

            key=lambda item:

                item["purchase_probability"],

            reverse=True

        )

        return recommendations

    # ---------------------------------------
    # Confidence Score
    # ---------------------------------------

    def confidence_score(self):

        total_orders = self.memory.total_completed_orders()

        if total_orders == 0:

            return 0

        if total_orders < 5:

            return 30

        if total_orders < 20:

            return 60

        if total_orders < 50:

            return 80

        return 95

    # ---------------------------------------
    # AI Context
    # ---------------------------------------

    def execute(self):

        return {

            "generated_at":

                datetime.now().strftime(

                    "%Y-%m-%d %H:%M:%S"

                ),

            "forecast_window":

                "Next 7 Days",

            "confidence":

                self.confidence_score(),

            "recommended_orders":

                self.forecast()

        }

    # ---------------------------------------
    # CLI Formatter
    # ---------------------------------------

    def format_report(self):

        data = self.execute()

        report = []

        report.append("=" * 60)

        report.append("        GROVIO PROCUREMENT FORECAST")

        report.append("=" * 60)

        report.append("")

        report.append(

            f"Forecast Window : {data['forecast_window']}"

        )

        report.append(

            f"Confidence      : {data['confidence']}%"

        )

        report.append("")

        if not data["recommended_orders"]:

            report.append(

                "No purchase history available."

            )

        else:

            for item in data["recommended_orders"]:

                report.append(

                    item["product"]

                )

                report.append(

                    f"Purchase Probability : {item['purchase_probability']}%"

                )

                report.append(

                    f"Suggested Quantity   : {item['recommended_quantity']}"

                )

                report.append(

                    f"Reason               : {item['reason']}"

                )

                report.append("")

        report.append("=" * 60)

        return "\n".join(report)


if __name__ == "__main__":

    forecaster = ProcurementForecaster()

    print(

        forecaster.format_report()

    )