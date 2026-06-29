import asyncio

from ai.agents.manual_order_agent import ManualOrderAgent


async def main():

    agent = ManualOrderAgent()

    result = await agent.execute(

        "9999999999",

        """Order

5 milk

2 butter
"""

    )

    print()

    print(

        result["message"]

    )


asyncio.run(main())