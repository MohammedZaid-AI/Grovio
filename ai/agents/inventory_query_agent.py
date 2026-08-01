import re
from db import get_product_inventory
from core.formatters import format_quantity


class InventoryQueryAgent:
    """
    Handles single-product inventory stock queries.

    Returns quick, direct answers for queries like:
    - "What's our paneer stock?"
    - "How much milk do we have?"
    - "Current butter inventory?"
    """

    # Words that are never a product — if the "product" is made only of these,
    # the user is asking a general question, not about one item.
    _STOP = {
        "what", "whats", "is", "my", "our", "the", "a", "an", "show", "me", "all",
        "list", "current", "running", "low", "on", "of", "do", "we", "have", "in",
        "stock", "inventory", "give", "tell", "status", "level", "levels", "item",
        "items", "and", "everything", "full", "entire", "whole", "much", "how",
    }

    def execute(self, message: str) -> dict:
        msg_lower = message.lower().strip()

        # Intent: what's low / needs reordering -> list low-stock items
        if re.search(r"\b(low|reorder|replenish|below\s+min\w*)\b", msg_lower) or "out of stock" in msg_lower:
            return {"message": self._format_low()}

        # Extract product name from query
        product_name = self._extract_product_name(msg_lower)

        # Intent: general "what is my inventory" / "show stock" -> list everything.
        if not product_name or all(w in self._STOP for w in product_name.split()):
            return {"message": self._format_all()}

        # Query inventory
        inventory_row = get_product_inventory(product_name)

        if not inventory_row:
            return {
                "message": f"❌ No inventory record for *{product_name}*. Product may not be tracked yet."
            }

        # Format response
        product, current_stock, minimum_stock, unit = inventory_row[1], inventory_row[2], inventory_row[3], inventory_row[4]

        current_stock_fmt = format_quantity(current_stock)
        reply = f"📦 *{product}*: {current_stock_fmt} {unit}"

        if minimum_stock:
            minimum_stock_fmt = format_quantity(minimum_stock)
            reply += f" (min: {minimum_stock_fmt} {unit})"

        if current_stock < minimum_stock if minimum_stock else False:
            reply += f"\n⚠️ Low stock! Below minimum threshold."

        return {
            "message": reply
        }

    def _format_all(self) -> str:
        from db import get_inventory
        rows = get_inventory()
        if not rows:
            return "📦 Inventory is empty. Add products on the dashboard or via 'add 5 kg paneer'."
        lines = ["📦 *Current Inventory*", ""]
        for r in rows:
            name, stock, minimum, unit = r[1], r[2], r[3], r[4]
            flag = " ⚠️" if (minimum and stock is not None and stock <= minimum) else ""
            lines.append(f"• {name}: {format_quantity(stock)} {unit}{flag}")
        return "\n".join(lines)

    def _format_low(self) -> str:
        from db import get_low_stock_items
        rows = get_low_stock_items()
        if not rows:
            return "✅ Nothing is below its minimum stock level."
        lines = ["⚠️ *Low Stock*", ""]
        for r in rows:
            name, stock, minimum, unit = r[1], r[2], r[3], r[4]
            lines.append(f"• {name}: {format_quantity(stock)} {unit} (min {format_quantity(minimum)})")
        return "\n".join(lines)

    def _extract_product_name(self, message: str) -> str:
        """Extract product name from query."""
        patterns = [
            r"(?:what's?|how much)\s+(?:our\s+)?([a-zA-Z\s]+?)(?:\s+stock|\s+do we have|\s+inventory|\?|$)",
            r"(?:current|check)\s+([a-zA-Z\s]+?)\s+(?:stock|inventory|\?|$)",
            r"([a-zA-Z\s]+?)\s+(?:stock|inventory|level)",
        ]

        for pattern in patterns:
            match = re.search(pattern, message, re.IGNORECASE)
            if match:
                product = match.group(1).strip()
                if product and len(product) > 1:
                    return product

        return None
