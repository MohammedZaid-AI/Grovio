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
        self.already_ordered = []

    # -----------------------------------
    # Forecast
    # -----------------------------------

    def forecast(self):

        self.already_ordered = []

        self.memory.refresh()

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

        from db import get_incoming_non_received_inventory_item, get_product_memory

        for product, quantity in counter.items():
            expected_date = get_incoming_non_received_inventory_item(product)
            if expected_date:
                self.already_ordered.append({
                    "product": product,
                    "expected_date": expected_date
                })
                continue

            # Reorder interval check
            mem = get_product_memory(product)
            if mem and mem['confidence_level'] == 'HIGH' and mem['avg_reorder_interval'] and mem['last_purchase_date']:
                from datetime import datetime, timedelta
                try:
                    last_date_part = mem['last_purchase_date'].split()[0]
                    last_date = datetime.strptime(last_date_part, "%Y-%m-%d")
                    days_elapsed = (datetime.now() - last_date).days
                    expected_interval = mem['avg_reorder_interval']
                    if days_elapsed < expected_interval - 1:
                        expected_order_date = (last_date + timedelta(days=expected_interval)).strftime("%Y-%m-%d")
                        self.already_ordered.append({
                            "product": product,
                            "expected_date": expected_order_date,
                            "not_due": True
                        })
                        continue
                except Exception as e:
                    print(f"Error checking reorder interval for {product}: {e}")

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
        rec_orders = self.forecast()
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

                rec_orders,

            "already_ordered":

                self.already_ordered

        }


if __name__ == "__main__":

    from pprint import pprint

    forecaster = ProcurementForecaster()

    pprint(

        forecaster.execute()

    )