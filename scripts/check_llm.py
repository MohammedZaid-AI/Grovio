"""
Can the configured LLM actually drive this product?

Two questions, and the second is the one that matters:

  1. How long does a turn take?
  2. Does it emit a TOOL CALL when it should?

The whole concierge hangs off tool calling. A model that chats beautifully but
never calls `find_food` cannot search, cannot order, and cannot be trusted with
anything — it would simply talk. That failure is silent in normal use, because
the reply still looks like a reply.

    venv\\Scripts\\python.exe scripts\\check_llm.py

Reads the same configuration the app does, so what it measures is what runs.
No network beyond the LLM, and nothing is ordered.
"""
import asyncio
import os
import sys
import time

sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv

load_dotenv()

from ai.planner import TOOLS
from core.config import Config
from core.llm import get_llm

# Things a user would plausibly say, and the tool each should reach for.
CASES = [
    ("I want biryani", "find_food"),
    ("I need milk", "find_food"),
    ("actually I'm allergic to peanuts", "remember"),
    ("where's my order?", "check_order"),
]

SYSTEM = (
    "You are a food concierge on WhatsApp. Use the tools available to you. "
    "Never invent a restaurant, price or delivery time."
)


async def main():
    llm = get_llm()
    print(f"\n  model     {Config.MODEL}")
    print(f"  endpoint  {llm.base_url}")
    print(f"  tools     {len(TOOLS)}\n")

    timings, correct = [], 0
    for message, expected in CASES:
        started = time.perf_counter()
        try:
            reply = await llm.chat(
                [{"role": "system", "content": SYSTEM},
                 {"role": "user", "content": message}],
                tools=TOOLS,
            )
        except Exception as e:
            print(f"  ❌ {message!r}\n     {type(e).__name__}: {e}")
            continue

        elapsed = time.perf_counter() - started
        timings.append(elapsed)
        called = [c.name for c in reply.tool_calls]

        if expected in called:
            correct += 1
            print(f"  ✅ {message!r:38} → {called}  {elapsed:.1f}s")
        elif called:
            print(f"  ⚠️  {message!r:38} → {called}, expected {expected}  {elapsed:.1f}s")
        else:
            print(f"  ❌ {message!r:38} → NO TOOL CALL  {elapsed:.1f}s")
            print(f"      said: {reply.text[:90]!r}")

    print()
    if timings:
        print(f"  median {sorted(timings)[len(timings) // 2]:.1f}s per call, "
              f"slowest {max(timings):.1f}s")
    print(f"  tool calls correct: {correct}/{len(CASES)}")

    if correct < len(CASES):
        print(
            "\n  ⚠️  This model does not reliably call tools. The concierge cannot\n"
            "      search or order without them — it would only ever chat.\n"
            "      Try a model known for tool use (qwen3, llama3.3) before shipping it."
        )
    # A turn can chain up to MAX_STEPS calls, so per-call latency is not the
    # number the user feels.
    if timings and sorted(timings)[len(timings) // 2] > 8:
        print("\n  ⚠️  Slow. A turn may chain several calls, so multiply this.")

    return 0 if correct == len(CASES) else 1


sys.exit(asyncio.run(main()))
