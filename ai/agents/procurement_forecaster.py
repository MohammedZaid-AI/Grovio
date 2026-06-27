from collections import Counter
from datetime import datetime

from ai.intelligence.procurement_memory import ProcurementMemory


class ProcurementForecaster:
    """
    Forecasts future procurement
    using purchase invoice history.
    """

    def __init__(self):

        self.memory = ProcurementMemory()

    # -----------------------------------
    # Forecast
    # -----------------------------------

    def forecast(self):

        counter = Counter()

        total = 0

        for product in self.memory.products:

            history = self.memory.product_history(product)

            quantity = len(history)

            counter[product] += quantity

            total += quantity

        if total == 0:

            return []

        recommendations = []

        for product, quantity in counter.items():

            probability = round(

                (quantity / total) * 100,

                2

            )

            recommendations.append(

                {

                    "product": product,

                    "purchase_probability": probability,

                    "recommended_quantity": max(
                        1,
                        quantity
                    ),

                    "reason":

                        f"Purchased {quantity} time(s) previously."

                }

            )

        recommendations.sort(

            key=lambda x: x["purchase_probability"],

            reverse=True

        )

        return recommendations

    # -----------------------------------
    # Confidence
    # -----------------------------------

    def confidence_score(self):

        invoices = self.memory.total_invoices()

        if invoices == 0:
            return 0

        if invoices < 5:
            return 30

        if invoices < 20:
            return 60

        if invoices < 50:
            return 80

        return 95

    # -----------------------------------
    # Execute
    # -----------------------------------

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


if __name__ == "__main__":

    from pprint import pprint

    forecaster = ProcurementForecaster()

    pprint(

        forecaster.execute()

    )