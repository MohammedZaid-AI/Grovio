import asyncio

from ai.agents.auto_order_agent import AutoOrderAgent


async def main():

    agent = AutoOrderAgent()

    result = await agent.execute(

        phone="9999999999"

    )

    print(result["message"])


asyncio.run(main())