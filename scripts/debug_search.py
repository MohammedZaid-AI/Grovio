"""Dump the RAW Swiggy MCP search response so we can see where 'products' live.

    python scripts/debug_search.py coke
"""
import asyncio, json, os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from dotenv import load_dotenv
load_dotenv()
from integrations.swiggy.swiggy_mcp import SwiggyInstamart


def dump(o):
    try:
        return json.dumps(o, indent=2, default=str)[:4000]
    except Exception:
        return repr(o)[:4000]


async def main():
    q = sys.argv[1] if len(sys.argv) > 1 else "coke"
    s = await SwiggyInstamart().initialize()
    addr = await s.get_address_id()
    print("address_id       :", addr)
    r = await s.search_product(addr, q)
    print("isError          :", getattr(r, "isError", None))
    print("top-level attrs  :", [a for a in dir(r) if not a.startswith("_")][:25])
    print("structuredContent:", dump(getattr(r, "structuredContent", None)))
    c = getattr(r, "content", None)
    if c:
        for i, part in enumerate(c):
            print(f"content[{i}].type :", getattr(part, "type", None))
            print(f"content[{i}].text :", (getattr(part, "text", None) or "")[:4000])
    else:
        print("content          : None")


if __name__ == "__main__":
    asyncio.run(main())
