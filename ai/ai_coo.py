import os
import json

from dotenv import load_dotenv
from groq import Groq

from ai.memory import RestaurantMemory
from ai.procurement_forecaster import ProcurementForecaster
from ai.pattern_detector import PatternDetector
from ai.daily_brief import generate_daily_brief

load_dotenv()


class AICOO:

    def __init__(self):

        self.client = Groq(
            api_key=os.getenv("GROQ_API_KEY")
        )

        self.memory = RestaurantMemory()

        self.forecaster = ProcurementForecaster()

        self.patterns = PatternDetector()


    def collect_restaurant_data(self):

        return {

            "completed_orders":
                self.memory.total_completed_orders(),

            "pending_orders":
                self.memory.total_pending_orders(),

            "restaurant_spend":
                self.memory.total_spend(),

            "average_order":
                self.memory.average_order_value(),

            "favorite_purchase_day":
                self.memory.favorite_purchase_day(),

            "most_purchased_products":
                self.memory.most_purchased_products(),

            "today_recurring":
                self.memory.upcoming_recurring_orders(),

            "forecast":
                self.forecaster.forecast(),

            "patterns":
                self.patterns.detect_day_patterns(),

            "daily_brief":
                generate_daily_brief()

        }


    def analyze(self):

        restaurant = self.collect_restaurant_data()

        prompt = f"""
You are Grovio AI COO.

You ONLY use the provided data.

If there isn't enough information for a section,
say "Insufficient historical data."

Based ONLY on the available data answer:

1. What do we know today?
2. What purchasing patterns are visible?
3. Which recurring orders need attention?
4. Which products are most frequently purchased?
5. What should the owner monitor over the next few days?
6. What additional data would improve your recommendations?

Never invent supplier information,
inventory levels,
profit margins,
menu items,
or stock levels.
"""

        response = self.client.chat.completions.create(

            model="openai/gpt-oss-20b",

            temperature=0.3,

            messages=[

                {

                    "role":"system",

                    "content":"You are an experienced restaurant COO."

                },

                {

                    "role":"user",

                    "content":prompt

                }

            ]

        )

        return response.choices[0].message.content


if __name__ == "__main__":

    ai = AICOO()

    report = ai.analyze()

    print(report)