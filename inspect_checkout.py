import asyncio
from mcp_use import MCPClient


async def main():

    client = MCPClient.from_config_file("mcp.json")

    sessions = await client.create_all_sessions()

    session = sessions["instamart"]

    tools = await session.list_tools()

    for tool in tools:

        if "checkout" in tool.name.lower():
            print(tool)


asyncio.run(main())