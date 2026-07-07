import re
from db import get_product_inventory


class InventoryQueryAgent:
    """
    Handles single-product inventory stock queries.

    Returns quick, direct answers for queries like:
    - "What's our paneer stock?"
    - "How much milk do we have?"
    - "Current butter inventory?"
    """

    def execute(self, message: str) -> dict:
        msg_lower = message.lower().strip()

        # Extract product name from query
        product_name = self._extract_product_name(msg_lower)

        if not product_name:
            return {
                "message": "❌ Could not determine which product you're asking about. Try: 'What's our milk stock?'"
            }

        # Query inventory
        inventory_row = get_product_inventory(product_name)

        if not inventory_row:
            return {
                "message": f"❌ No inventory record for *{product_name}*. Product may not be tracked yet."
            }

        # Format response
        product, current_stock, minimum_stock, unit = inventory_row[1], inventory_row[2], inventory_row[3], inventory_row[4]

        reply = f"📦 *{product}*: {current_stock} {unit}"

        if minimum_stock:
            reply += f" (min: {minimum_stock} {unit})"

        if current_stock < minimum_stock if minimum_stock else False:
            reply += f"\n⚠️ Low stock! Below minimum threshold."

        return {
            "message": reply
        }

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
