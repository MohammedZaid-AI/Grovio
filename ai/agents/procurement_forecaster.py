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

            # Check inventory levels for emergency stock override
            from db import get_product_inventory, get_product_consumption_stats
            inv = get_product_inventory(product)
            current_stock = 0.0
            minimum_stock = 0.0
            if inv:
                current_stock = inv[2] if inv[2] is not None else 0.0
                minimum_stock = inv[3] if inv[3] is not None else 0.0

            # Calculate sales-based consumption stock duration
            stats = get_product_consumption_stats(product)
            adcr = stats.get('adcr')
            adcr_confidence = stats.get('adcr_confidence')
            
            days_left = None
            if adcr_confidence == 'HIGH' and adcr and adcr > 0:
                days_left = current_stock / adcr

            is_physical_low = (minimum_stock > 0.0) and (current_stock <= minimum_stock)
            is_consumption_low = (days_left is not None and days_left <= 2.0)
            is_emergency = is_physical_low or is_consumption_low

            # Checks only run if not in emergency stock outage (either physical or sales-based)
            if not is_emergency:
                mem = get_product_memory(product)
                if mem:
                    not_due_skip = False
                    wrong_day_skip = False
                    expected_order_date = None
                    usual_day_name = None

                    # 1. Reorder interval check
                    if mem.get('confidence_level') == 'HIGH' and mem.get('avg_reorder_interval') and mem.get('last_purchase_date'):
                        from datetime import datetime, timedelta
                        try:
                            last_date_part = mem['last_purchase_date'].split()[0]
                            last_date = datetime.strptime(last_date_part, "%Y-%m-%d")
                            days_elapsed = (datetime.now() - last_date).days
                            expected_interval = mem['avg_reorder_interval']
                            if days_elapsed < expected_interval - 1:
                                not_due_skip = True
                                expected_order_date = (last_date + timedelta(days=expected_interval)).strftime("%Y-%m-%d")
                        except Exception as e:
                            print(f"Error checking reorder interval for {product}: {e}")

                    # 2. Day of week check
                    if mem.get('usual_day_of_week_confidence') == 'HIGH' and mem.get('usual_day_of_week'):
                        current_day = datetime.now().strftime("%A")
                        usual_day_name = mem['usual_day_of_week']
                        if current_day != usual_day_name:
                            wrong_day_skip = True

                    # Skip decision and priority mapping
                    if not_due_skip or wrong_day_skip:
                        self.already_ordered.append({
                            "product": product,
                            "expected_date": expected_order_date,
                            "not_due": not_due_skip,
                            "wrong_day": wrong_day_skip,
                            "usual_day": usual_day_name
                        })
                        continue

            probability = round(
                (quantity / total) * 100,
                2
            )

            # Determine reason message based on emergency overrides
            if is_consumption_low:
                reason = f"Low stock alert ({days_left:.1f} days left based on sales consumption)"
            elif is_physical_low:
                reason = f"Low stock alert (physical stock {current_stock:.1f} left, min is {minimum_stock:.1f})"
            else:
                reason = f"Purchased {quantity} time(s) previously."

            recommendations.append(
                {
                    "product": product,
                    "purchase_probability": probability,
                    "recommended_quantity": max(
                        1,
                        quantity
                    ),
                    "reason": reason
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