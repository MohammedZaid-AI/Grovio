import json
import os

from dotenv import load_dotenv
from groq import Groq

from ai.intelligence.memory import RestaurantMemory
from ai.intelligence.pattern_detector import PatternDetector
from ai.agents.procurement_forecaster import ProcurementForecaster
from ai.reports.daily_brief import generate_daily_brief

load_dotenv()


class AICOO:
    """
    Executive AI of Grovio.

    Responsible only for reasoning.

    It never talks directly to the database.

    It receives structured context
    from other intelligence modules.
    """

    def __init__(self):

        self.client = Groq(

            api_key=os.getenv(

                "GROQ_API_KEY"

            )

        )

    # -----------------------------------
    # Build Restaurant Context
    # -----------------------------------

    def build_context(self):

        return {

            "memory":

                RestaurantMemory().execute(),

            "patterns":

                PatternDetector().execute(),

            "forecast":

                ProcurementForecaster().execute(),

            "daily_brief":

                generate_daily_brief()

        }

    # -----------------------------------
    # AI Analysis
    # -----------------------------------

    def analyze(self):

        context = self.build_context()

        prompt = f"""
You are the AI COO of a restaurant.

Below is the current restaurant context.

{json.dumps(context, indent=2, default=str)}

Your job is to act like an experienced restaurant COO.

ONLY use the supplied data.

Never invent information.

Never assume inventory.

Never assume suppliers.

Never assume profits.

Produce a structured report with these sections.

1. Executive Summary

2. Restaurant Health

3. Purchasing Behaviour

4. Procurement Forecast

5. Risks

6. Opportunities

7. Recommendations

8. Suggested Actions For Tomorrow

Keep the response practical.

Use bullet points whenever possible.
"""

        response = self.client.chat.completions.create(

            model="openai/gpt-oss-20b",

            temperature=0.2,

            messages=[

                {

                    "role": "system",

                    "content": "You are an experienced restaurant COO."

                },

                {

                    "role": "user",

                    "content": prompt

                }

            ]

        )

        return {

            "context": context,

            "analysis":

                response

                .choices[0]

                .message

                .content

        }

    # -----------------------------------
    # CLI Report
    # -----------------------------------

    def format_report(self):

        result = self.analyze()

        report = []

        report.append("=" * 70)

        report.append("              GROVIO AI COO")

        report.append("=" * 70)

        report.append("")

        report.append(

            result["analysis"]

        )

        report.append("")

        report.append("=" * 70)

        return "\n".join(report)


if __name__ == "__main__":

    ai = AICOO()

    print(

        ai.format_report()

    )