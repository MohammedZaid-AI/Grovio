from collections import Counter
from datetime import datetime

from ai.memory import RestaurantMemory

class PatternDetector:

    def __init__(self):

        self.memory = RestaurantMemory()

    def detect_purchase_frequency(self):

        history = self.memory.history

        frequency = {}

        for order in history:

            product = order[1]

            date = datetime.strptime(
                order[5],
                "%Y-%m-%d %H:%M:%S"
            )

            day = date.strftime("%A")

            if product not in frequency:

                frequency[product] = []

            frequency[product].append(day)

        return frequency

    def detect_day_patterns(self):

        frequency = self.detect_purchase_frequency()

        patterns = []

        for product, days in frequency.items():

            counter = Counter(days)

            favourite_day, count = counter.most_common(1)[0]

            confidence = round(
                count / len(days) * 100,
                1
            )

            patterns.append({

                "product": product,

                "day": favourite_day,

                "count": len(days),

                "confidence": confidence

            })

        return patterns

    def detect_top_products(self):

        return self.memory.most_purchased_products()

    def generate_report(self):

        print()

        print("=" * 55)

        print("           GROVIO PATTERN REPORT")

        print("=" * 55)

        print()

        print("TOP PURCHASED PRODUCTS")

        print()

        products = self.detect_top_products()

        if not products:

            print("No purchase history available.")

        else:

            for product, qty in products:

                print(
                    f"{product} ({qty})"
                )

        print()

        print("-" * 55)

        print()

        print("PURCHASE PATTERNS")

        print()

        patterns = self.detect_day_patterns()

        if not patterns:

            print("No patterns discovered yet.")

        else:

            for pattern in patterns:

                print(
                    f"{pattern['product']}"
                )

                print(
                    f"Usually purchased on "
                    f"{pattern['day']}"
                )

                print(
                    f"Occurrences : "
                    f"{pattern['count']}"
                )

                print(
                    f"Confidence : "
                    f"{pattern['confidence']}%"
                )

                print()

        print("=" * 55)
        print()


if __name__ == "__main__":

    detector = PatternDetector()

    detector.generate_report()