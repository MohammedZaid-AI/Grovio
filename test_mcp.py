import asyncio
from mcp_use import MCPClient

async def main():
    client = MCPClient.from_config_file("mcp.json")

    sessions = await client.create_all_sessions()

    print("Sessions:")
    print(sessions)

    tools = await client.search_tools("")
    print("Tools:")
    print(tools)

asyncio.run(main())