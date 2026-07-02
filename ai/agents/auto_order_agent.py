from ai.intelligence.procurement_planner import ProcurementPlanner


class AutoOrderAgent:
    """
    Generates today's procurement plan.

    It does NOT start shopping.
    It only prepares the list and returns it.
    """

    def __init__(self):

        self.planner = ProcurementPlanner()

    async def execute(self,message=None):

        plan = await self.planner.execute()

        manual_items = []

        if message:

            import re

            pattern = r"(\d+)\s+(.+)"

            for line in message.splitlines():

                line = line.strip()

                match = re.match(pattern, line)

                if match:

                    manual_items.append(

                        {

                            "name": match.group(2),

                            "quantity": int(match.group(1))

                        }

                    )

        # ------------------------------------
        # Merge AI + Manual Items
        # ------------------------------------

        merged = {}

        for item in plan["items"]:

            name = item["name"].lower()

            merged[name] = {

                "name": item["name"],

                "quantity": item["quantity"]

            }

        for item in manual_items:

            name = item["name"].lower()

            if name in merged:

                merged[name]["quantity"] += item["quantity"]

            else:

                merged[name] = {

                    "name": item["name"],

                    "quantity": item["quantity"]

                }

        plan["items"] = list(

            merged.values()

        )

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