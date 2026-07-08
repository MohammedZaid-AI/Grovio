"""
Diagnostic: discover the REAL Swiggy MCP tool schema for payment options.

Run in your live environment (network + Swiggy auth):

    python scripts/inspect_swiggy_payment.py

It:
  1. Lists every MCP tool + its input schema (so we see the exact payment tool
     name and the checkout tool's argument names).
  2. Probes candidate payment tools with a few argument shapes and prints the
     RAW result (isError / text / structuredContent), so we learn what works.

Paste the full output back and I'll pin the code to the real schema.
"""
import asyncio
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from integrations.swiggy.swiggy_mcp import SwiggyInstamart


def _dump(obj):
    try:
        return json.dumps(obj, indent=2, default=str)
    except Exception:
        return repr(obj)


async def main():
    swiggy = await SwiggyInstamart().initialize()
    session = swiggy.session

    # ---- 1. List all tools + schemas ----
    print("=" * 70)
    print("ALL MCP TOOLS")
    print("=" * 70)
    tools_result = await session.list_tools()
    tools = getattr(tools_result, "tools", tools_result)
    tool_names = []
    for t in tools:
        name = getattr(t, "name", None)
        desc = getattr(t, "description", "")
        schema = getattr(t, "inputSchema", None) or getattr(t, "input_schema", None)
        tool_names.append(name)
        print(f"\n--- {name} ---")
        print(f"description: {desc}")
        print(f"inputSchema: {_dump(schema)}")

    print("\n" + "=" * 70)
    print("TOOL NAMES:", tool_names)
    print("=" * 70)

    # ---- 2. Probe candidate payment tools / argument shapes ----
    try:
        address_id = await swiggy.get_address_id()
    except Exception as e:
        address_id = None
        print(f"\n(could not fetch address_id: {e})")

    candidate_names = [n for n in tool_names if "pay" in (n or "").lower()]
    if not candidate_names:
        candidate_names = ["get_payment_options", "get_payment_methods", "payment_options"]

    print(f"\nCandidate payment tools to probe: {candidate_names}")

    arg_shapes = [
        ("no args", {}),
        ("addressId", {"addressId": address_id}),
        ("selectedAddressId", {"selectedAddressId": address_id}),
    ]

    for tool_name in candidate_names:
        for label, args in arg_shapes:
            print("\n" + "-" * 60)
            print(f"CALL {tool_name}  ({label}) args={args}")
            try:
                result = await session.call_tool(tool_name, args)
                print("isError:", getattr(result, "isError", None))
                content = getattr(result, "content", None)
                if content and getattr(content[0], "text", None):
                    print("content[0].text:", content[0].text)
                print("structuredContent:", _dump(getattr(result, "structuredContent", None)))
            except Exception as e:
                print(f"EXCEPTION: {type(e).__name__}: {e}")


if __name__ == "__main__":
    asyncio.run(main())
