from db import get_price_history


class PriceTracker:

    def analyze(self, product):

        history = get_price_history(product)

        if len(history) < 2:

            return "Not enough history."

        latest = history[0]

        previous = history[1]

        latest_price = latest[1]

        previous_price = previous[1]

        change = latest_price - previous_price

        percent = (change / previous_price) * 100

        if change > 0:

            return (

                f"{product} price increased "

                f"by ₹{change:.2f} "

                f"({percent:.1f}%)"

            )

        elif change < 0:

            return (

                f"{product} price dropped "

                f"by ₹{abs(change):.2f} "

                f"({abs(percent):.1f}%)"

            )

        return f"{product} price unchanged."

        def latest_price(self, product):
            """
            Returns the latest known price
            for a product.

            Used by the Purchase Order Generator.
            """

            history = get_price_history(product)

            if not history:

                return 0.0

            return history[0][1]

if __name__ == "__main__":

    tracker = PriceTracker()

    print(

        tracker.analyze(

            "Nandini Milk"

        )

    )