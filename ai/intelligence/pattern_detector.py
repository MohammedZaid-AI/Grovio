from collections import Counter
from datetime import datetime

from db import get_order_history


class PatternDetector:

    """
    Detects purchasing behaviour
    from historical order data.
    """

    def __init__(self):

        self.history = get_order_history()

    # ------------------------------------
    # Product Purchase Frequency
    # ------------------------------------

    def product_frequency(self):

        counter = Counter()

        for order in self.history:

            product = order[1]

            quantity = order[2]

            counter[product] += quantity

        return counter.most_common()

    # ------------------------------------
    # Purchase Day Patterns
    # ------------------------------------

    def detect_day_patterns(self):

        patterns = {}

        for order in self.history:

            product = order[1]

            date = datetime.strptime(

                order[5],

                "%Y-%m-%d %H:%M:%S"

            )

            day = date.strftime("%A")

            if product not in patterns:

                patterns[product] = []

            patterns[product].append(day)

        results = []

        for product, days in patterns.items():

            counter = Counter(days)

            most_common = counter.most_common(1)[0]

            confidence = round(

                (most_common[1] / len(days)) * 100,

                2

            )

            results.append(

                {

                    "product": product,

                    "usual_day": most_common[0],

                    "occurrences": most_common[1],

                    "total_orders": len(days),

                    "confidence": confidence

                }

            )

        return results

    # ------------------------------------
    # Weekend vs Weekday
    # ------------------------------------

    def weekend_behavior(self):

        weekends = 0

        weekdays = 0

        for order in self.history:

            date = datetime.strptime(

                order[5],

                "%Y-%m-%d %H:%M:%S"

            )

            day = date.strftime("%A")

            if day in [

                "Saturday",

                "Sunday"

            ]:

                weekends += 1

            else:

                weekdays += 1

        return {

            "weekday_orders": weekdays,

            "weekend_orders": weekends

        }

    # ------------------------------------
    # Recommendations
    # ------------------------------------

    def recommendations(self):

        recommendations = []

        patterns = self.detect_day_patterns()

        for pattern in patterns:

            if pattern["confidence"] >= 70:

                recommendations.append(

                    f"{pattern['product']} is usually purchased on {pattern['usual_day']}."

                )

        if not recommendations:

            recommendations.append(

                "Not enough purchasing history."

            )

        return recommendations

    # ------------------------------------
    # Main Entry
    # ------------------------------------

    def execute(self):

        return {

            "top_products":

                self.product_frequency(),

            "weekly_patterns":

                self.detect_day_patterns(),

            "weekend_behavior":

                self.weekend_behavior(),

            "recommendations":

                self.recommendations()

        }


if __name__ == "__main__":

    from pprint import pprint

    detector = PatternDetector()

    pprint(

        detector.execute()

    )