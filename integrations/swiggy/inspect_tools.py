import asyncio
from mcp.swiggy_mcp import SwiggyInstamart

async def main():

    swiggy = await SwiggyInstamart().initialize()

    tools = await swiggy.session.list_tools()

    print(type(tools))

    print("\nTOOLS:\n")

    for tool in tools:
        print(tool)

asyncio.run(main())