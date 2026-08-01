"""
Dump the live MCP tool surface.

Run this to confirm what the Swiggy endpoint actually exposes — specifically
whether anything restaurant-shaped (venue search, menus, ratings, ETA, order
tracking) exists, or only the Instamart grocery tools. See MIGRATION.md §0.

    PYTHONPATH=. python integrations/swiggy/inspect_tools.py
"""
import asyncio

from integrations.swiggy.swiggy_mcp import SwiggyInstamart


async def main():
    swiggy = await SwiggyInstamart().initialize()
    tools = await swiggy.session.list_tools()

    print(f"\n{len(tools)} TOOLS:\n")
    for tool in tools:
        print(f"  {getattr(tool, 'name', tool)}")
        desc = getattr(tool, "description", None)
        if desc:
            print(f"      {desc}")


if __name__ == "__main__":
    asyncio.run(main())
