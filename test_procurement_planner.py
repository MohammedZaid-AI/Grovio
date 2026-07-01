import asyncio

from ai.intelligence.procurement_planner import ProcurementPlanner


async def main():

    planner = ProcurementPlanner()

    result = await planner.execute()

    print(result)


asyncio.run(main())