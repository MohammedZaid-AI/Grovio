from pprint import pprint

from db import (
    create_purchase_order,
    add_purchase_order_item
)

from ai.procurement.purchase_order_generator import PurchaseOrderGenerator


class PurchaseOrderService:
    """
    Generates a smart purchase order and
    stores it in the database.
    """

    def __init__(self):

        self.generator = PurchaseOrderGenerator()

    def generate_preview(self):
        purchase_order = self.generator.generate()
        forecast = self.generator.forecast.execute()
        reasons = {item["product"]: item.get("reason", "") for item in forecast.get("recommended_orders", [])}
        return {
            "purchase_order_id": "PREVIEW",
            "supplier": purchase_order.supplier,
            "items": [
                {
                    "product": item.product,
                    "quantity": item.quantity,
                    "unit": item.unit,
                    "price": item.estimated_price,
                    "subtotal": item.subtotal,
                    "reason": reasons.get(item.product, "")
                }
                for item in purchase_order.items
            ],
            "total_items": purchase_order.total_items,
            "total_quantity": purchase_order.total_quantity,
            "total": purchase_order.total_amount
        }

    # --------------------------------------------------
    # Generate + Save Purchase Order
    # --------------------------------------------------

    def create(self):
        purchase_order = self.generator.generate()
        forecast = self.generator.forecast.execute()
        reasons = {item["product"]: item.get("reason", "") for item in forecast.get("recommended_orders", [])}

        # Deduplication check: Reuse existing identical draft PO from last 5 minutes
        from db import get_connection
        from datetime import datetime, timedelta
        conn = get_connection()
        try:
            cursor = conn.cursor()
            five_mins_ago = (datetime.now() - timedelta(minutes=5)).strftime('%Y-%m-%d %H:%M:%S')
            cursor.execute('''
                SELECT id FROM purchase_orders
                WHERE supplier=? AND status='DRAFT' AND created_at >= ?
                ORDER BY id DESC LIMIT 1
            ''', (purchase_order.supplier, five_mins_ago))
            existing_po = cursor.fetchone()
            
            if existing_po:
                existing_id = existing_po[0]
                cursor.execute('''
                    SELECT product, quantity FROM purchase_order_items
                    WHERE purchase_order_id=?
                ''', (existing_id,))
                existing_items = sorted(cursor.fetchall())
                new_items = sorted([(item.product, item.quantity) for item in purchase_order.items])
                
                if existing_items == new_items:
                    print(f"⚠️ Duplicate draft PO detected (ID: {existing_id}). Reusing existing draft PO.")
                    return {
                        "purchase_order_id": existing_id,
                        "supplier": purchase_order.supplier,
                        "items": [
                            {
                                "product": item.product,
                                "quantity": item.quantity,
                                "unit": item.unit,
                                "price": item.estimated_price,
                                "subtotal": item.subtotal,
                                "reason": reasons.get(item.product, "")
                            }
                            for item in purchase_order.items
                        ],
                        "total_items": purchase_order.total_items,
                        "total_quantity": purchase_order.total_quantity,
                        "total": purchase_order.total_amount
                    }
        except Exception as e:
            print(f"Error checking duplicate draft PO: {e}")
        finally:
            conn.close()

        purchase_order_id = create_purchase_order(

            supplier=purchase_order.supplier,

            total_amount=purchase_order.total_amount

        )

        # --------------------------------------------
        # Save Purchase Order Items
        # --------------------------------------------

        for item in purchase_order.items:

            add_purchase_order_item(

                purchase_order_id,

                item.product,

                item.quantity,

                item.unit,

                item.estimated_price,

                item.subtotal

            )

        # --------------------------------------------
        # Return Complete Purchase Order
        # --------------------------------------------

        return {
            "purchase_order_id": purchase_order_id,
            "supplier": purchase_order.supplier,
            "items": [
                {
                    "product": item.product,
                    "quantity": item.quantity,
                    "unit": item.unit,
                    "price": item.estimated_price,
                    "subtotal": item.subtotal,
                    "reason": reasons.get(item.product, "")
                }
                for item in purchase_order.items
            ],
            "total_items": purchase_order.total_items,
            "total_quantity": purchase_order.total_quantity,
            "total": purchase_order.total_amount
        }


# --------------------------------------------------
# Testing
# --------------------------------------------------

if __name__ == "__main__":

    service = PurchaseOrderService()

    result = service.create()

    print()

    print("=" * 60)

    print("PURCHASE ORDER")

    print("=" * 60)

    pprint(result)

    print("=" * 60)