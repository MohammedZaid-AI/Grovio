import re
from datetime import datetime
from db import (
    get_product_inventory,
    save_inventory,
    update_inventory,
    log_inventory_audit,
)
from core.formatters import format_quantity


class InventoryManagerAgent:
    """
    Handles manual inventory adjustments via SET (absolute) and ADD/REMOVE (delta) commands.

    Supports:
    - SET absolute stock: "Set paneer stock to 10 kg, minimum 2 kg"
    - ADD delta: "Add 5 kg paneer"
    - REMOVE delta: "Remove 2.5 L milk"

    Features:
    - Unit mismatch detection with explicit confirmation required
    - Floating-point display formatting
    - Audit trail logging
    - Fail-safe validation (no negative stock, requires unit)
    """

    def execute(self, message: str, user_phone: str = None) -> dict:
        msg_lower = message.lower().strip()

        # Detect SET command (absolute)
        set_match = self._parse_set_command(msg_lower)
        if set_match:
            return self._handle_set_command(set_match, message, user_phone)

        # Detect ADD/REMOVE commands (delta)
        delta_match = self._parse_delta_command(msg_lower)
        if delta_match:
            return self._handle_delta_command(delta_match, message, user_phone)

        return {
            "message": "❌ Could not parse inventory command.\n\n"
                       "Try:\n"
                       "• SET: \"Set paneer stock to 10 kg, minimum 2 kg\"\n"
                       "• ADD: \"Add 5 kg paneer\"\n"
                       "• REMOVE: \"Remove 2.5 L milk\""
        }

    def _parse_set_command(self, message: str) -> dict:
        """
        Parse SET command: "Set [product] stock to [qty] [unit][, minimum [qty] [unit]]"

        Returns: {product, qty, unit, minimum_qty, minimum_unit, confirm_unit_change} or None
        """
        # Pattern: set [product] stock to [number] [unit] [, minimum [number] [unit]]
        pattern = r"set\s+([a-z\s]+?)\s+stock\s+to\s+([\d.]+)\s*([a-z]+)(?:,?\s+(?:min|minimum):?\s+([\d.]+)\s*([a-z]+))?"
        match = re.search(pattern, message, re.IGNORECASE)

        if not match:
            return None

        product = match.group(1).strip()
        qty_str = match.group(2)
        unit = match.group(3).strip().lower()
        min_qty_str = match.group(4)
        min_unit = match.group(5).strip().lower() if match.group(5) else None
        confirm_unit_change = "confirm unit change" in message

        # Validate
        if not product or len(product) < 2:
            return None
        if not self._is_valid_unit(unit):
            return None

        try:
            qty = float(qty_str)
            min_qty = float(min_qty_str) if min_qty_str else None
        except ValueError:
            return None

        if qty < 0:
            return None

        return {
            "product": product,
            "qty": qty,
            "unit": unit,
            "minimum_qty": min_qty,
            "minimum_unit": min_unit or unit,
            "confirm_unit_change": confirm_unit_change,
        }

    def _parse_delta_command(self, message: str) -> dict:
        """
        Parse ADD/REMOVE command: "[add|remove] [number] [unit] [product]"

        Returns: {action, qty, unit, product, confirm_unit_change} or None
        """
        # Pattern: (add|remove) [number] [unit] [product]  (unit may be glued: "10kg")
        pattern = r"(add|remove|subtract)\s+([\d.]+)\s*([a-z]+)\s+([a-z\s]+)"
        match = re.search(pattern, message, re.IGNORECASE)

        if not match:
            return None

        action = match.group(1).strip().lower()
        if action == "subtract":
            action = "remove"

        qty_str = match.group(2)
        unit = match.group(3).strip().lower()
        product = match.group(4).strip()
        confirm_unit_change = "confirm unit change" in message

        # Validate
        if not product or len(product) < 2:
            return None
        if not self._is_valid_unit(unit):
            return None

        try:
            qty = float(qty_str)
        except ValueError:
            return None

        if qty < 0:
            return None

        return {
            "action": action,
            "qty": qty,
            "unit": unit,
            "product": product,
            "confirm_unit_change": confirm_unit_change,
        }

    def _handle_set_command(self, cmd, raw_message: str, user_phone: str) -> dict:
        """Handle SET (absolute) command."""
        product = cmd["product"]
        qty = cmd["qty"]
        unit = cmd["unit"]
        min_qty = cmd["minimum_qty"]
        min_unit = cmd["minimum_unit"]
        confirm_unit_change = cmd["confirm_unit_change"]

        # Get existing product
        existing = get_product_inventory(product)

        # Check unit mismatch
        if existing:
            old_stock, old_unit = existing[2], existing[4]
            old_minimum = existing[3]

            if old_unit.lower() != unit:
                if not confirm_unit_change:
                    return {
                        "message": f"⚠️ *Unit Mismatch*\n\n"
                                   f"{product} was previously tracked in *{old_unit}*, "
                                   f"but you specified *{unit}*.\n\n"
                                   f"To confirm this intentional unit change, re-send:\n"
                                   f"\"Set {product} stock to {qty} {unit}, confirm unit change\""
                    }
                # User confirmed - proceed with unit change
        else:
            old_stock = old_minimum = None
            old_unit = None

        # Validate no negative stock
        if qty < 0:
            return {
                "message": f"❌ Stock cannot be negative. Please enter a positive quantity."
            }

        # If minimum not specified, keep existing
        if min_qty is None and existing:
            min_qty = existing[3]
            min_unit = existing[4]

        # Save to inventory
        save_inventory(product, qty, min_qty, unit)

        # Log audit
        log_inventory_audit(
            product_name=product,
            action_type="SET_ABSOLUTE",
            old_stock=old_stock,
            new_stock=qty,
            old_unit=old_unit,
            new_unit=unit,
            old_minimum=old_minimum,
            new_minimum=min_qty,
            source="whatsapp",
            user_phone=user_phone,
            notes=raw_message,
        )

        # Format response
        qty_fmt = format_quantity(qty)
        reply = f"✅ *{product}* stock set to {qty_fmt} {unit}"

        if min_qty:
            min_fmt = format_quantity(min_qty)
            reply += f"\n📊 Minimum threshold: {min_fmt} {unit}"

        if old_unit and old_unit.lower() != unit:
            reply += f"\n⚠️ Unit changed from {old_unit} → {unit}"

        return {"message": reply}

    def _handle_delta_command(self, cmd, raw_message: str, user_phone: str) -> dict:
        """Handle ADD/REMOVE (delta) command."""
        action = cmd["action"]  # add or remove
        qty = cmd["qty"]
        unit = cmd["unit"]
        product = cmd["product"]
        confirm_unit_change = cmd["confirm_unit_change"]

        # Get existing product
        existing = get_product_inventory(product)

        if not existing:
            return {
                "message": f"❌ No inventory record for *{product}*. "
                           f"Please create it first with: \"Set {product} stock to [qty] {unit}\""
            }

        old_stock = existing[2]
        old_unit = existing[4]
        old_minimum = existing[3]

        # Check unit mismatch
        if old_unit.lower() != unit:
            if not confirm_unit_change:
                return {
                    "message": f"⚠️ *Unit Mismatch*\n\n"
                               f"{product} is tracked in *{old_unit}*, "
                               f"but you specified *{unit}*.\n\n"
                               f"To confirm, re-send:\n"
                               f"\"{action.capitalize()} {qty} {unit} {product}, confirm unit change\""
                }
            # User confirmed - proceed (but DON'T auto-convert units)

        # Calculate new stock
        if action == "add":
            new_stock = old_stock + qty
        else:  # remove
            new_stock = old_stock - qty

        # Validate no negative stock
        if new_stock < 0:
            return {
                "message": f"❌ Cannot remove {qty}. Current stock is {format_quantity(old_stock)} {old_unit}. "
                           f"This would result in negative stock."
            }

        # Update inventory (quantity_change parameter)
        if action == "add":
            update_inventory(product, qty)
        else:  # remove
            update_inventory(product, -qty)

        # Log audit
        log_inventory_audit(
            product_name=product,
            action_type="ADJUST_DELTA",
            old_stock=old_stock,
            new_stock=new_stock,
            old_unit=old_unit,
            new_unit=unit if old_unit.lower() != unit else old_unit,
            old_minimum=old_minimum,
            new_minimum=old_minimum,
            source="whatsapp",
            user_phone=user_phone,
            notes=f"{action.upper()} delta: {qty} {unit}",
        )

        # Format response
        old_fmt = format_quantity(old_stock)
        new_fmt = format_quantity(new_stock)
        action_emoji = "➕" if action == "add" else "➖"

        reply = f"{action_emoji} *{product}*: {old_fmt} {old_unit} → {new_fmt} {old_unit}"

        if new_stock < old_minimum if old_minimum else False:
            reply += f"\n⚠️ Now below minimum ({format_quantity(old_minimum)} {old_unit})"

        return {"message": reply}

    def _is_valid_unit(self, unit: str) -> bool:
        """Check if unit is recognized."""
        valid_units = {
            "kg", "g", "mg", "lbs", "lb",
            "l", "ml", "litre", "liter",
            "pieces", "piece", "pcs", "pc",
            "packets", "packet", "pk",
            "boxes", "box", "btl", "bottle",
            "dozen", "dz", "unit", "units",
        }
        return unit.lower() in valid_units
