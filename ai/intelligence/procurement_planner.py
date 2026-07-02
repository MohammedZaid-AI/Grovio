from ai.intelligence.procurement_context_builder import context_builder
from ai.agents.procurement_planner_agent import procurement_planner


class ProcurementPlanner:
    """
    AI Procurement Planner.

    Responsibilities:

    1. Build restaurant context.
    2. Ask the AI planner.
    3. Return the procurement plan.
    """

    async def execute(self):

        context = await context_builder.build()

        plan = await procurement_planner.execute(

            context

        )

        return plan


planner = ProcurementPlanner()