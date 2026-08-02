"""
Dump a Swiggy MCP server's real tool surface.

Run this to find out exactly what a server exposes, so adapters are written
against real tools instead of guesses.

    PYTHONPATH=. python integrations/swiggy/inspect_tools.py food
    PYTHONPATH=. python integrations/swiggy/inspect_tools.py im
    PYTHONPATH=. python integrations/swiggy/inspect_tools.py dineout

Paste the output when asking for an adapter to be written.
"""
import asyncio
import json
import sys

from mcp_use import MCPClient

SERVERS = {
    "food": "https://mcp.swiggy.com/food",
    "im": "https://mcp.swiggy.com/im",
    "instamart": "https://mcp.swiggy.com/im",
    "dineout": "https://mcp.swiggy.com/dineout",
}


async def main():
    which = (sys.argv[1] if len(sys.argv) > 1 else "food").lower()
    url = SERVERS.get(which, which)   # allow a raw URL too

    print(f"\nConnecting to {url}")
    print("(a browser may open for Swiggy authorisation — localhost is whitelisted)\n")

    client = MCPClient.from_dict({"mcpServers": {"target": {"url": url}}})
    try:
        sessions = await client.create_all_sessions()
        session = sessions["target"]
        tools = await session.list_tools()
    except Exception as e:
        print(f"❌ could not connect: {type(e).__name__}: {e}")
        print("\nIf this is an auth error, the server requires approved access.")
        return

    print(f"{len(tools)} TOOLS\n" + "=" * 66)
    for tool in tools:
        name = getattr(tool, "name", str(tool))
        print(f"\n▸ {name}")
        desc = (getattr(tool, "description", "") or "").strip()
        if desc:
            print(f"    {desc[:300]}")
        schema = getattr(tool, "inputSchema", None) or getattr(tool, "input_schema", None)
        if isinstance(schema, dict) and schema.get("properties"):
            required = set(schema.get("required") or [])
            print("    params:")
            for key, spec in schema["properties"].items():
                kind = spec.get("type", "?") if isinstance(spec, dict) else "?"
                mark = "*" if key in required else " "
                print(f"      {mark} {key} ({kind})")

    print("\n" + "=" * 66)
    print("Copy everything above.\n")

    try:
        await client.close_all_sessions()
    except Exception:
        pass


asyncio.run(main())
