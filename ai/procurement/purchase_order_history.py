from db import (
    get_purchase_orders,
    get_purchase_order_items_by_order
)


class PurchaseOrderHistory:

    def all_orders(self):
        from db import get_connection
        conn = get_connection()
        try:
            cursor = conn.cursor()
            
            # Fetch from purchase_orders (legacy/mock POs)
            cursor.execute('''
                SELECT id, supplier, status, total_amount, created_at
                FROM purchase_orders
            ''')
            po_rows = cursor.fetchall()
            
            # Fetch from orders (Swiggy orders)
            cursor.execute("PRAGMA table_info(orders)")
            cols = [r[1] for r in cursor.fetchall()]
            swiggy_rows = []
            if 'order_id' in cols:
                cursor.execute('''
                    SELECT order_id, phone, status, total, created_at
                    FROM orders
                    WHERE order_id IS NOT NULL
                ''')
                swiggy_rows = cursor.fetchall()
            
            # Combine them
            combined = []
            for r in po_rows:
                combined.append({
                    "id": r[0],
                    "supplier": r[1],
                    "status": r[2],
                    "total": r[3],
                    "created_at": r[4]
                })
            for r in swiggy_rows:
                supplier = f"Swiggy Instamart ({r[1]})" if r[1] else "Swiggy Instamart"
                combined.append({
                    "id": r[0],
                    "supplier": supplier,
                    "status": r[2],
                    "total": r[3] if r[3] is not None else 0,
                    "created_at": r[4]
                })
            
            # Sort by created_at DESC
            combined.sort(key=lambda x: x["created_at"] or "", reverse=True)
            
            return [(o["id"], o["supplier"], o["status"], o["total"], o["created_at"]) for o in combined]
        finally:
            conn.close()

    def latest_order(self):

        orders = self.all_orders()

        if not orders:

            return None

        return orders[0]

    def by_status(self, status):

        orders = []

        for order in self.all_orders():

            if order[2].upper() == status.upper():

                orders.append(order)

        return orders

    def details(self, purchase_order_id):
        from db import get_connection
        import json
        conn = get_connection()
        try:
            cursor = conn.cursor()
            
            # First try looking up as a Swiggy order in orders table
            cursor.execute("PRAGMA table_info(orders)")
            cols = [r[1] for r in cursor.fetchall()]
            if 'order_id' in cols:
                cursor.execute('SELECT items FROM orders WHERE order_id=?', (str(purchase_order_id),))
                row = cursor.fetchone()
                if row and row[0]:
                    try:
                        return json.loads(row[0])
                    except Exception:
                        pass
            
            # If not found, try legacy purchase_order_items
            cursor.execute('''
                SELECT product, quantity, unit, estimated_price, subtotal
                FROM purchase_order_items
                WHERE purchase_order_id=?
            ''', (purchase_order_id,))
            rows = cursor.fetchall()
            return rows
        finally:
            conn.close()

    def execute(self):

        latest = self.latest_order()

        if latest is None:

            return {

                "message": "No purchase orders found."

            }

        items = self.details(

            latest[0]

        )

        return {

            "purchase_order_id": latest[0],

            "supplier": latest[1],

            "status": latest[2],

            "total": latest[3],

            "created_at": latest[4],

            "items": items

        }


if __name__ == "__main__":

    from pprint import pprint

    history = PurchaseOrderHistory()

    pprint(

        history.execute()

    )