from collections import Counter
from datetime import datetime, timedelta

from ai.memory import RestaurantMemory


class ProcurementForecaster:

    def __init__(self):

        self.memory = RestaurantMemory()

    def forecast(self):

        history = self.memory.history

        if not history:

            return []

        counter = Counter()

        for order in history:

            product = order[1]

            counter[product] += order[2]

        recommendations = []

        total_orders = len(history)

        for product, qty in counter.items():

            probability = round(
                qty / total_orders * 100,
                1
            )

            recommendations.append({

                "product": product,

                "probability": probability,

                "recommended_quantity": max(
                    1,
                    round(qty / total_orders)
                )

            })

        recommendations.sort(
            key=lambda x: x["probability"],
            reverse=True
        )

        return recommendations

    def generate_report(self):

        print()

        print("=" * 60)

        print("        GROVIO PROCUREMENT FORECAST")

        print("=" * 60)

        print()

        print(
            "Forecast Window : Next 7 Days"
        )

        print()

        forecasts = self.forecast()

        if not forecasts:

            print(
                "No purchase history available."
            )

            return

        for item in forecasts:

            print(
                f"{item['product']}"
            )

            print(
                f"Purchase Probability : "
                f"{item['probability']}%"
            )

            print(
                f"Suggested Quantity   : "
                f"{item['recommended_quantity']}"
            )

            print()

        print("=" * 60)


if __name__ == "__main__":

    ProcurementForecaster().generate_report()