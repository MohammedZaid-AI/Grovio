from ai.agents.order_parser_agent import order_parser
from ai.intelligence.procurement_planner import ProcurementPlanner


class AutoOrderAgent:
    """
    Entry point for a shopping request.

    It keeps two intents strictly separate:

      • MANUAL SHOPPING — the user listed explicit items. Grovio orders EXACTLY
        those items. Low-inventory items are NEVER merged in automatically;
        Grovio may only *mention* them as a suggestion.

      • RESTAURANT REPLENISHMENT — the user asked to restock with no explicit
        list. Grovio runs the procurement planner (inventory + forecast).

    A single parser (OrderParserAgent, LLM) decides which intent applies: if it
    extracts explicit items, it is manual shopping; if not, it is replenishment.
    """

    def __init__(self):
        self.planner = ProcurementPlanner()

    async def execute(self, message=None):

        explicit_items = []
        if message:
            explicit_items = await order_parser.execute(message) or []

        if explicit_items:
            return self._manual(explicit_items)

        return await self._replenishment()

    # ------------------------------------------------------------------
    # Manual shopping — honour the request exactly
    # ------------------------------------------------------------------
    def _manual(self, explicit_items):
        items = [
            {"name": it["name"], "quantity": it.get("quantity", 1)}
            for it in explicit_items
            if it.get("name")
        ]

        reply = ["🛒 *Shopping List*", ""]
        for it in items:
            reply.append(f"• {it['name']} × {it['quantity']}")
        reply.append("")
        reply.append("Reply YES to start shopping.")

        suggestion = self._low_stock_suggestion(items)
        if suggestion:
            reply.append("")
            reply.append(suggestion)

        return {
            "mode": "manual",
            "items": items,
            "message": "\n".join(reply),
        }

    # ------------------------------------------------------------------
    # Replenishment — inventory + forecast planner
    # ------------------------------------------------------------------
    async def _replenishment(self):
        plan = await self.planner.execute()
        items = plan.get("items", [])

        reply = ["🧠 *Today's Procurement Plan*", ""]
        for it in items:
            reply.append(f"• {it['name']} × {it['quantity']}")
        reply.append("")
        reply.append(f"Confidence : {plan.get('confidence', 0)}%")
        if plan.get("reasoning"):
            reply.append("")
            reply.append("Reasoning:")
            for reason in plan["reasoning"]:
                reply.append(f"• {reason}")
        reply.append("")
        reply.append("Reply YES to start shopping.")

        return {
            "mode": "replenishment",
            "items": items,
            "confidence": plan.get("confidence"),
            "reasoning": plan.get("reasoning"),
            "message": "\n".join(reply),
        }

    # ------------------------------------------------------------------
    # Low-stock suggestion — ASK, never merge
    # ------------------------------------------------------------------
    def _low_stock_suggestion(self, requested_items):
        try:
            from db import get_low_stock_items
            rows = get_low_stock_items()
        except Exception:
            return None

        requested_norm = {(it["name"] or "").strip().lower() for it in requested_items}

        low = []
        for row in rows:
            name = row[1]
            if not name:
                continue
            # Skip items the user already asked for (dedupe, not selection).
            if name.strip().lower() in requested_norm:
                continue
            low.append(name)

        if not low:
            return None

        names = ", ".join(low[:5])
        return (
            f"💡 Heads up — these are running low in inventory: {names}. "
            "Let me know if you'd like to add any of them."
        )


auto_order_agent = AutoOrderAgent()
