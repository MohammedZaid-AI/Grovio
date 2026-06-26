import json

from core.llm import llm

from ai.intelligence.memory import RestaurantMemory
from ai.intelligence.pattern_detector import PatternDetector
from ai.agents.procurement_forecaster import ProcurementForecaster
from ai.reports.daily_brief import generate_daily_brief


class AICOO:
    """
    Executive AI of Grovio.

    Responsible only for reasoning.

    It never talks directly to the database.

    It receives structured context from the
    intelligence layer and produces executive
    insights for the restaurant owner.
    """

    def __init__(self):

        self.memory = RestaurantMemory()

        self.patterns = PatternDetector()

        self.forecaster = ProcurementForecaster()

    # --------------------------------------------------
    # Build Context
    # --------------------------------------------------

    def build_context(self):

        return {

            "memory":

                self.memory.execute(),

            "patterns":

                self.patterns.execute(),

            "forecast":

                self.forecaster.execute(),

            "daily_brief":

                generate_daily_brief()

        }

    # --------------------------------------------------
    # AI Analysis
    # --------------------------------------------------

    def analyze(self):

        context = self.build_context()

        prompt = f"""
You are Grovio AI COO.

Below is the complete restaurant context.

{json.dumps(context, indent=2, default=str)}

You are NOT allowed to invent information.

Only use the supplied context.

Your responsibility is to think like an experienced
Restaurant COO.

Generate a report using the following sections.

1. Executive Summary

2. Restaurant Health

3. Purchasing Behaviour

4. Procurement Forecast

5. Business Risks

6. Opportunities

7. Recommendations

8. Actions For Tomorrow

Rules:

• Never invent suppliers.
• Never invent inventory.
• Never invent profits.
• Never invent menu items.
• Mention when historical data is limited.
• Keep recommendations practical.
• Use bullet points.
"""

        analysis = llm.chat(

            system="""
You are an experienced Restaurant COO.

You analyse restaurant operations,
procurement,
inventory,
and purchasing behaviour.

Always answer using the provided context only.
""",

            user=prompt

        )

        return {

            "context":

                context,

            "analysis":

                analysis

        }

    # --------------------------------------------------
    # CLI Formatter
    # --------------------------------------------------

    def format_report(self):

        result = self.analyze()

        report = []

        report.append("=" * 70)

        report.append("                 GROVIO AI COO")

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