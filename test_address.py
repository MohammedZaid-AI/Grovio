import asyncio
from mcp_use import MCPClient


async def main():

    client = MCPClient.from_config_file("mcp.json")

    sessions = await client.create_all_sessions()

    session = sessions["instamart"]

    result = await session.call_tool(
        "get_addresses",
        {}
    )

    print(result)


asyncio.run(main())