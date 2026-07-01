from ai.intelligence.procurement_planner import ProcurementPlanner


class AutoOrderAgent:
    """
    Generates today's procurement plan.

    It does NOT start shopping.
    It only prepares the list and returns it.
    """

    def __init__(self):

        self.planner = ProcurementPlanner()

    async def execute(self):

        plan = await self.planner.execute()

        reply = []

        reply.append("🧠 *Today's Procurement Plan*")

        reply.append("")

        for item in plan["items"]:

            reply.append(

                f"• {item['name']} × {item['quantity']}"

            )

        reply.append("")

        reply.append(

            f"Confidence : {plan['confidence']}%"

        )

        reply.append("")

        reply.append("Reasoning:")

        for reason in plan["reasoning"]:

            reply.append(

                f"• {reason}"

            )

        reply.append("")

        reply.append(

            "Reply YES to start shopping."

        )

        return {

            "items": plan["items"],

            "confidence": plan["confidence"],

            "reasoning": plan["reasoning"],

            "message": "\n".join(reply)

        }