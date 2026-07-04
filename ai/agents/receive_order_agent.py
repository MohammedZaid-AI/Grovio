import re
from datetime import datetime
from db import (
    get_open_purchase_order_by_supplier,
    get_incoming_inventory_for_po,
    update_incoming_inventory_item,
    add_to_inventory_stock,
    mark_delivery_delivered,
    transition_po_to_received_and_updated,
    get_product_inventory
)

class ReceiveOrderAgent:
    """
    Agent that processes receipt of goods for active purchase orders.
    Updates incoming_inventory and main inventory, and transitions PO states.
    """
    
    def parse_received_quantity(self, message, product_name):
        msg = " " + re.sub(r'\s+', ' ', message.lower()) + " "
        prod = product_name.lower()
        
        # Try matching: [number] [optional unit/words] [product]
        # E.g. "5 milk", "5 units of milk", "5 bottles of milk"
        pattern = r"(\d+(?:\.\d+)?)\s*(?:[a-zA-Z]+)?\s*(?:of\s*)?" + re.escape(prod)
        match = re.search(pattern, msg)
        if match:
            return float(match.group(1))
            
        return None

    def execute(self, message: str) -> str:
        if not message:
            return "No receipt details provided."

        # 1. Extract supplier from message (e.g. "from ABC Dairy")
        supplier_match = re.search(r"from\s+([a-zA-Z0-9\s_'-]+)", message, re.IGNORECASE)
        if not supplier_match:
            return "Could not determine the supplier name. Please specify using 'from [Supplier Name]'."
            
        supplier = supplier_match.group(1).strip()
        
        # 2. Retrieve open PO for this supplier
        po = get_open_purchase_order_by_supplier(supplier)
        if not po:
            return f"No active/approved purchase orders found for supplier '{supplier}'."
            
        po_id, current_status, actual_supplier = po
        
        # 3. Retrieve expected incoming items
        incoming_items = get_incoming_inventory_for_po(po_id)
        if not incoming_items:
            return f"No expected inventory items found for Purchase Order #{po_id}."
            
        received_items = []
        discrepancies = []
        new_items_warnings = []
        received_date = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        
        # Check if the user specified that the entire order arrived
        has_all_keyword = any(k in message.lower() for k in ["all items", "everything", "all products", "received all", "entire order", "arrived all"])
        
        # 4. Match and process items
        for item_id, product, expected_qty, unit, received_status, _ in incoming_items:
            if received_status == 1:
                continue
                
            received_qty = None
            if has_all_keyword:
                received_qty = float(expected_qty)
            else:
                # Try to parse numeric quantity
                parsed_qty = self.parse_received_quantity(message, product)
                if parsed_qty is not None:
                    received_qty = parsed_qty
                else:
                    # Check if the product name was mentioned but without a quantity
                    if product.lower() in message.lower():
                        return (
                            f"⚠️ I noticed you mentioned '{product}' received from {actual_supplier}, "
                            f"but the quantity was not specified. To ensure accurate discrepancy logging, "
                            f"please state the exact quantity (e.g., 'received 8 {product} from {actual_supplier}')."
                        )
            
            if received_qty is not None:
                # Check if item is new to inventory table before adding
                existing_stock_record = get_product_inventory(product)
                if not existing_stock_record:
                    new_items_warnings.append(
                        f"⚠️ '{product}' was added to inventory for the first time. "
                        f"Its minimum stock is currently set to 0.0. Please update it to enable low-stock alerts."
                    )
                
                # Update incoming_inventory record
                update_incoming_inventory_item(item_id, received_qty, received_date)
                
                # Update actual stock/inventory table
                add_to_inventory_stock(product, received_qty, unit)
                
                received_items.append(f"• {product}: {received_qty} {unit} (expected: {expected_qty})")
                
                # Log discrepancy if received differs from ordered
                if received_qty != expected_qty:
                    disc_msg = f"[DISCREPANCY_LOG] PO #{po_id} Item '{product}': Ordered {expected_qty}, Received {received_qty}"
                    print(disc_msg)
                    discrepancies.append(f"⚠️ Discrepancy logged for {product}: expected {expected_qty}, received {received_qty}")
            
        if not received_items:
            return f"None of the expected items for Purchase Order #{po_id} were found in your message."
            
        # 5. Advance PO status to RECEIVED, then to INVENTORY_UPDATED
        try:
            transition_po_to_received_and_updated(po_id)
            mark_delivery_delivered(po_id)
        except Exception as e:
            return f"Processed items, but failed to update order status: {e}"
            
        # 6. Formulate response
        response = [
            f"✅ *Purchase Order #{po_id} Receipt Processed*",
            f"Supplier: {actual_supplier}",
            f"Status updated: {current_status} ➔ INVENTORY_UPDATED",
            "\n*Received Items added to stock:*",
            "\n".join(received_items)
        ]
        
        if discrepancies:
            response.append("\n*Discrepancies Logged:*")
            response.extend(discrepancies)
            
        if new_items_warnings:
            response.append("\n*Inventory Warnings:*")
            response.extend(new_items_warnings)
            
        return "\n".join(response)
