import sqlite3
import json
DB_PATH = 'database/orders.db'

def get_connection():
    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA foreign_keys = ON")
    return conn

def init_db():
    conn = get_connection()
    try:
        cursor = conn.cursor()
        cursor.execute("\n    CREATE TABLE IF NOT EXISTS orders(\n\n        id INTEGER PRIMARY KEY AUTOINCREMENT,\n\n        product_name TEXT,\n\n        spin_id TEXT,\n\n        quantity INTEGER,\n\n        order_type TEXT,\n\n        schedule_time TEXT,\n\n        recurrence TEXT,\n\n        status TEXT DEFAULT 'active'\n\n    )\n    ")
        
        # Safe schema migration for orders table
        cursor.execute("PRAGMA table_info(orders)")
        columns = [row[1] for row in cursor.fetchall()]
        if 'order_id' not in columns:
            cursor.execute("ALTER TABLE orders ADD COLUMN order_id TEXT")
        if 'items' not in columns:
            cursor.execute("ALTER TABLE orders ADD COLUMN items TEXT")
        if 'total' not in columns:
            cursor.execute("ALTER TABLE orders ADD COLUMN total REAL")
        if 'phone' not in columns:
            cursor.execute("ALTER TABLE orders ADD COLUMN phone TEXT")
        if 'created_at' not in columns:
            cursor.execute("ALTER TABLE orders ADD COLUMN created_at TIMESTAMP")

        cursor.execute("\n    CREATE TABLE IF NOT EXISTS pending_orders(\n\n        id INTEGER PRIMARY KEY AUTOINCREMENT,\n\n        product_name TEXT,\n\n        spin_id TEXT,\n\n        quantity INTEGER,\n\n        created_at TIMESTAMP\n        DEFAULT CURRENT_TIMESTAMP,\n\n        status TEXT DEFAULT\n        'awaiting_confirmation'\n\n    )\n    ")
        cursor.execute('\n    CREATE TABLE IF NOT EXISTS order_history(\n\n        id INTEGER PRIMARY KEY AUTOINCREMENT,\n\n        product_name TEXT,\n\n        quantity INTEGER,\n\n        amount REAL,\n\n        order_id TEXT,\n\n        ordered_at TIMESTAMP\n        DEFAULT CURRENT_TIMESTAMP\n\n    )\n    ')
        cursor.execute('\n    CREATE TABLE IF NOT EXISTS purchase_invoices(\n\n        id INTEGER PRIMARY KEY AUTOINCREMENT,\n\n        supplier TEXT,\n\n        invoice_number TEXT,\n\n        invoice_date TEXT,\n\n        total_amount REAL,\n\n        created_at TIMESTAMP\n        DEFAULT CURRENT_TIMESTAMP\n\n    )\n    ')
        cursor.execute('\n    CREATE TABLE IF NOT EXISTS purchase_items(\n\n        id INTEGER PRIMARY KEY AUTOINCREMENT,\n\n        invoice_id INTEGER,\n\n        product TEXT,\n\n        quantity REAL,\n\n        unit TEXT,\n\n        unit_price REAL,\n\n        total REAL,\n\n        FOREIGN KEY(invoice_id)\n        REFERENCES purchase_invoices(id)\n\n    )\n    ')
        cursor.execute('\n    CREATE TABLE IF NOT EXISTS product_price_history(\n\n        id INTEGER PRIMARY KEY AUTOINCREMENT,\n\n        product TEXT,\n\n        supplier TEXT,\n\n        price REAL,\n\n        purchase_date TEXT\n\n    )\n    ')
        cursor.execute('\n    CREATE TABLE IF NOT EXISTS inventory(\n\n        id INTEGER PRIMARY KEY AUTOINCREMENT,\n\n        product_name TEXT UNIQUE,\n\n        current_stock REAL,\n\n        minimum_stock REAL,\n\n        unit TEXT,\n\n        is_active INTEGER DEFAULT 1,\n\n        updated_at TIMESTAMP\n        DEFAULT CURRENT_TIMESTAMP\n\n    )\n    ')

        # Safe schema migration for inventory table
        cursor.execute("PRAGMA table_info(inventory)")
        inv_columns = [row[1] for row in cursor.fetchall()]
        if 'is_active' not in inv_columns:
            cursor.execute("ALTER TABLE inventory ADD COLUMN is_active INTEGER DEFAULT 1")

        cursor.execute("\n    CREATE TABLE IF NOT EXISTS purchase_orders(\n\n        id INTEGER PRIMARY KEY AUTOINCREMENT,\n\n        supplier TEXT NOT NULL,\n\n        status TEXT DEFAULT 'DRAFT',\n\n        total_amount REAL DEFAULT 0,\n\n        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP\n\n    )\n    ")
        cursor.execute('\n    CREATE TABLE IF NOT EXISTS purchase_order_items(\n\n        id INTEGER PRIMARY KEY AUTOINCREMENT,\n\n        purchase_order_id INTEGER,\n\n        product TEXT,\n\n        quantity REAL,\n\n        unit TEXT,\n\n        estimated_price REAL,\n\n        subtotal REAL,\n\n        FOREIGN KEY(purchase_order_id)\n            REFERENCES purchase_orders(id)\n\n    )\n    ')

        cursor.execute('''
        CREATE TABLE IF NOT EXISTS expected_deliveries (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            purchase_order_id INTEGER NOT NULL,
            supplier TEXT NOT NULL,
            expected_date TEXT NOT NULL,
            status TEXT DEFAULT 'PENDING',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY(purchase_order_id) REFERENCES purchase_orders(id)
        )
        ''')

        cursor.execute('''
        CREATE TABLE IF NOT EXISTS incoming_inventory (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            purchase_order_id INTEGER NOT NULL,
            product TEXT NOT NULL,
            quantity REAL NOT NULL,
            unit TEXT NOT NULL,
            expected_date TEXT NOT NULL,
            received INTEGER DEFAULT 0,
            received_date TEXT,
            received_quantity REAL,
            FOREIGN KEY(purchase_order_id) REFERENCES purchase_orders(id)
        )
        ''')

        cursor.execute('''
        CREATE TABLE IF NOT EXISTS product_memory (
            product_name TEXT PRIMARY KEY,
            preferred_brand TEXT,
            preferred_brand_source TEXT DEFAULT 'AUTO',
            preferred_supplier TEXT,
            preferred_supplier_source TEXT DEFAULT 'AUTO',
            avg_reorder_interval REAL,
            last_purchase_date TEXT,
            confidence_level TEXT DEFAULT 'NONE',
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        ''')

        # Safe schema migration for product_memory table
        cursor.execute("PRAGMA table_info(product_memory)")
        pm_columns = [row[1] for row in cursor.fetchall()]
        if 'usual_day_of_week' not in pm_columns:
            cursor.execute("ALTER TABLE product_memory ADD COLUMN usual_day_of_week TEXT")
        if 'usual_day_of_week_confidence' not in pm_columns:
            cursor.execute("ALTER TABLE product_memory ADD COLUMN usual_day_of_week_confidence TEXT DEFAULT 'NONE'")
        if 'seasonal_spikes' not in pm_columns:
            cursor.execute("ALTER TABLE product_memory ADD COLUMN seasonal_spikes TEXT")

        cursor.execute('''
        CREATE TABLE IF NOT EXISTS supplier_reliability (
            supplier_name TEXT PRIMARY KEY,
            total_deliveries INTEGER DEFAULT 0,
            on_time_deliveries INTEGER DEFAULT 0,
            accuracy_rate REAL,
            avg_delay_days REAL,
            quantity_accuracy_rate REAL,
            confidence_level TEXT DEFAULT 'NONE',
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        ''')

        cursor.execute('''
        CREATE TABLE IF NOT EXISTS pending_documents (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            phone TEXT NOT NULL,
            doc_type TEXT NOT NULL,
            payload TEXT NOT NULL,
            status TEXT DEFAULT 'PENDING',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        ''')

        cursor.execute('''
        CREATE TABLE IF NOT EXISTS recipes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            dish_name TEXT NOT NULL,
            ingredient_name TEXT NOT NULL,
            quantity_per_unit REAL NOT NULL,
            unit TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(dish_name, ingredient_name)
        )
        ''')

        cursor.execute('''
        CREATE TABLE IF NOT EXISTS sales_bills (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            bill_number TEXT UNIQUE,
            bill_date TEXT NOT NULL,
            total_amount REAL NOT NULL,
            status TEXT DEFAULT 'PENDING_CONFIRMATION',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        ''')

        cursor.execute('''
        CREATE TABLE IF NOT EXISTS sales_bill_items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            bill_id INTEGER NOT NULL,
            dish_name TEXT NOT NULL,
            quantity INTEGER NOT NULL,
            unit_price REAL,
            total_price REAL,
            FOREIGN KEY(bill_id) REFERENCES sales_bills(id)
        )
        ''')

        cursor.execute('''
        CREATE TABLE IF NOT EXISTS product_consumption (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            product_name TEXT NOT NULL,
            consumed_quantity REAL NOT NULL,
            unit TEXT NOT NULL,
            calculation_date TEXT NOT NULL,
            source_bill_id INTEGER,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY(source_bill_id) REFERENCES sales_bills(id)
        )
        ''')

        # Safe schema migration for product_consumption table (add status column)
        cursor.execute("PRAGMA table_info(product_consumption)")
        pc_columns = [row[1] for row in cursor.fetchall()]
        if 'status' not in pc_columns:
            cursor.execute("ALTER TABLE product_consumption ADD COLUMN status TEXT DEFAULT 'PENDING'")

        cursor.execute('''
        CREATE TABLE IF NOT EXISTS pending_inventory_deductions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ingredient_name TEXT NOT NULL,
            estimated_quantity REAL NOT NULL,
            unit TEXT NOT NULL,
            source_sales_bill_id INTEGER NOT NULL,
            status TEXT DEFAULT 'PENDING',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY(source_sales_bill_id) REFERENCES sales_bills(id)
        )
        ''')

        cursor.execute('''
        CREATE TABLE IF NOT EXISTS inventory_audit_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            product_name TEXT NOT NULL,
            action_type TEXT NOT NULL,
            old_stock REAL,
            new_stock REAL,
            old_unit TEXT,
            new_unit TEXT,
            old_minimum REAL,
            new_minimum REAL,
            source TEXT NOT NULL,
            user_phone TEXT,
            notes TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        ''')

        conn.commit()
    finally:
        conn.close()

def save_order(product_name, spin_id, quantity, order_type, schedule_time=None, recurrence=None):
    conn = get_connection()
    try:
        conn.execute('\n        INSERT INTO orders(\n\n            product_name,\n\n            spin_id,\n\n            quantity,\n\n            order_type,\n\n            schedule_time,\n\n            recurrence\n\n        )\n\n        VALUES (?, ?, ?, ?, ?, ?)\n\n        ', (product_name, spin_id, quantity, order_type, schedule_time, recurrence))
        conn.commit()
    finally:
        conn.close()

def save_swiggy_order(order_id, items, total, status, phone):
    conn = get_connection()
    try:
        from datetime import datetime
        created_at = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        items_json = json.dumps(items)
        conn.execute('''
        INSERT INTO orders(
            order_id,
            items,
            total,
            status,
            phone,
            order_type,
            created_at
        )
        VALUES (?, ?, ?, ?, ?, 'swiggy', ?)
        ''', (order_id, items_json, total, status, phone, created_at))
        conn.commit()
    finally:
        conn.close()

def get_orders() -> list:
    conn = get_connection()
    try:
        rows = conn.execute('\n        SELECT *\n        FROM orders\n        ').fetchall()
        return rows
    finally:
        conn.close()

def save_pending_order(product_name, spin_id, quantity):
    conn = get_connection()
    try:
        conn.execute('\n\n        INSERT INTO pending_orders(\n\n            product_name,\n\n            spin_id,\n\n            quantity\n\n        )\n\n        VALUES (?, ?, ?)\n\n        ', (product_name, spin_id, quantity))
        conn.commit()
    finally:
        conn.close()

def get_pending_orders():
    conn = get_connection()
    try:
        rows = conn.execute("\n\n        SELECT *\n\n        FROM pending_orders\n\n        WHERE status='awaiting_confirmation'\n\n        ").fetchall()
        return rows
    finally:
        conn.close()

def pending_exists(product_name, spin_id):
    conn = get_connection()
    try:
        row = conn.execute("\n\n        SELECT id\n\n        FROM pending_orders\n\n        WHERE product_name=?\n\n        AND spin_id=?\n\n        AND status='awaiting_confirmation'\n\n        ", (product_name, spin_id)).fetchone()
        return row is not None
    finally:
        conn.close()

def mark_pending_completed(pending_id):
    conn = get_connection()
    try:
        conn.execute("\n\n        UPDATE pending_orders\n\n        SET status='completed'\n\n        WHERE id=?\n\n        ", (pending_id,))
        conn.commit()
    finally:
        conn.close()

def save_order_history(product_name, quantity, amount, order_id):
    conn = get_connection()
    try:
        conn.execute('\n\n        INSERT INTO order_history(\n\n            product_name,\n\n            quantity,\n\n            amount,\n\n            order_id\n\n        )\n\n        VALUES (?, ?, ?, ?)\n\n        ', (product_name, quantity, amount, order_id))
        conn.commit()
    finally:
        conn.close()

def get_order_history():
    conn = get_connection()
    try:
        rows = conn.execute('\n\n        SELECT *\n\n        FROM order_history\n\n        ORDER BY id DESC\n\n        ').fetchall()
        return rows
    finally:
        conn.close()

def create_purchase_order(supplier, total_amount):
    conn = get_connection()
    try:
        cursor = conn.cursor()
        cursor.execute('\n        INSERT INTO purchase_orders(\n            supplier,\n            total_amount\n        )\n        VALUES(?,?)\n        ', (supplier, total_amount))
        purchase_order_id = cursor.lastrowid
        conn.commit()
        return purchase_order_id
    finally:
        conn.close()

def add_purchase_order_item(purchase_order_id, product, quantity, unit, estimated_price, subtotal):
    conn = get_connection()
    try:
        cursor = conn.cursor()
        cursor.execute('\n        INSERT INTO purchase_order_items(\n\n            purchase_order_id,\n            product,\n            quantity,\n            unit,\n            estimated_price,\n            subtotal\n\n        )\n        VALUES(?,?,?,?,?,?)\n        ', (purchase_order_id, product, quantity, unit, estimated_price, subtotal))
        conn.commit()
    finally:
        conn.close()

def get_purchase_orders():
    conn = get_connection()
    try:
        cursor = conn.cursor()
        cursor.execute('\n\n        SELECT\n            id,\n            supplier,\n            status,\n            total_amount,\n            created_at\n\n        FROM purchase_orders\n\n        ORDER BY created_at DESC\n\n    ')
        rows = cursor.fetchall()
        return rows
    finally:
        conn.close()

def save_invoice(invoice):
    conn = get_connection()
    try:
        cursor = conn.cursor()
        cursor.execute('\n\n        INSERT INTO purchase_invoices(\n\n            supplier,\n\n            invoice_number,\n\n            invoice_date,\n\n            total_amount\n\n        )\n\n        VALUES (?, ?, ?, ?)\n\n        ', (invoice['supplier'], invoice['invoice_number'], invoice['date'], invoice['total_amount']))
        invoice_id = cursor.lastrowid
        for item in invoice['items']:
            cursor.execute('\n\n            INSERT INTO purchase_items(\n\n                invoice_id,\n\n                product,\n\n                quantity,\n\n                unit,\n\n                unit_price,\n\n                total\n\n            )\n\n            VALUES (?, ?, ?, ?, ?, ?)\n\n            ', (invoice_id, item['product'], item['quantity'], item['unit'], item['unit_price'], item['total']))
            cursor.execute('\n\n            INSERT INTO product_price_history(\n\n                product,\n\n                supplier,\n\n                price,\n\n                purchase_date\n\n            )\n\n            VALUES (?, ?, ?, ?)\n\n            ', (item['product'], invoice['supplier'], item['unit_price'], invoice['date']))
        conn.commit()
    finally:
        conn.close()

def get_invoices():
    conn = get_connection()
    try:
        rows = conn.execute('\n\n        SELECT *\n\n        FROM purchase_invoices\n\n        ORDER BY id DESC\n\n        ').fetchall()
        return rows
    finally:
        conn.close()

def get_invoice_items(invoice_id):
    conn = get_connection()
    try:
        rows = conn.execute('\n\n        SELECT *\n\n        FROM purchase_items\n\n        WHERE invoice_id=?\n\n        ', (invoice_id,)).fetchall()
        return rows
    finally:
        conn.close()

def get_price_history(product):
    conn = get_connection()
    try:
        rows = conn.execute('\n        SELECT\n\n            supplier,\n\n            price,\n\n            purchase_date\n\n        FROM product_price_history\n\n        WHERE product=?\n\n        ORDER BY purchase_date DESC\n        ', (product,)).fetchall()
        return rows
    finally:
        conn.close()

def get_latest_price(product):
    conn = get_connection()
    try:
        row = conn.execute('\n        SELECT\n\n            supplier,\n\n            price,\n\n            purchase_date\n\n        FROM product_price_history\n\n        WHERE product=?\n\n        ORDER BY purchase_date DESC\n\n        LIMIT 1\n        ', (product,)).fetchone()
        return row
    finally:
        conn.close()

def get_supplier_prices(product):
    conn = get_connection()
    try:
        rows = conn.execute('\n        SELECT\n\n            supplier,\n\n            AVG(price)\n\n        FROM product_price_history\n\n        WHERE product=?\n\n        GROUP BY supplier\n        ', (product,)).fetchall()
        return rows
    finally:
        conn.close()

def get_cheapest_supplier(product):
    suppliers = get_supplier_prices(product)
    if not suppliers:
        return None
    return min(suppliers, key=lambda x: x[1])

def get_top_suppliers():
    conn = get_connection()
    try:
        rows = conn.execute('\n        SELECT\n\n            supplier,\n\n            COUNT(*)\n\n        FROM purchase_invoices\n\n        GROUP BY supplier\n\n        ORDER BY COUNT(*) DESC\n        ').fetchall()
        return rows
    finally:
        conn.close()

def get_supplier_statistics():
    conn = get_connection()
    try:
        rows = conn.execute('\n        SELECT\n\n            supplier,\n\n            COUNT(*) AS invoices,\n\n            SUM(total_amount)\n\n        FROM purchase_invoices\n\n        GROUP BY supplier\n\n        ORDER BY SUM(total_amount) DESC\n        ').fetchall()
        return rows
    finally:
        conn.close()

def get_total_spend_by_supplier(supplier):
    conn = get_connection()
    try:
        row = conn.execute('\n        SELECT\n\n            SUM(total_amount)\n\n        FROM purchase_invoices\n\n        WHERE supplier=?\n        ', (supplier,)).fetchone()
        return row[0] if row and row[0] else 0
    finally:
        conn.close()

def get_monthly_spend():
    conn = get_connection()
    try:
        rows = conn.execute('\n        SELECT\n\n            substr(invoice_date,1,7),\n\n            SUM(total_amount)\n\n        FROM purchase_invoices\n\n        GROUP BY substr(invoice_date,1,7)\n\n        ORDER BY substr(invoice_date,1,7)\n        ').fetchall()
        return rows
    finally:
        conn.close()

def get_product_purchase_history(product):
    conn = get_connection()
    try:
        rows = conn.execute('\n        SELECT\n\n            purchase_date,\n\n            supplier,\n\n            price\n\n        FROM product_price_history\n\n        WHERE product=?\n\n        ORDER BY purchase_date\n        ', (product,)).fetchall()
        return rows
    finally:
        conn.close()

def get_all_products():
    conn = get_connection()
    try:
        rows = conn.execute('\n        SELECT DISTINCT product\n\n        FROM purchase_items\n\n        ORDER BY product\n        ').fetchall()
        return [row[0] for row in rows]
    finally:
        conn.close()

def get_dashboard_stats():
    return {'orders': len(get_orders()), 'pending': len(get_pending_orders()), 'history': len(get_order_history()), 'invoices': len(get_invoices()), 'suppliers': len(get_top_suppliers()), 'products': len(get_all_products())}

def save_inventory(product_name, current_stock, minimum_stock, unit):
    conn = get_connection()
    try:
        conn.execute('\n        INSERT OR REPLACE INTO inventory(\n\n            product_name,\n\n            current_stock,\n\n            minimum_stock,\n\n            unit\n\n        )\n\n        VALUES (?, ?, ?, ?)\n        ', (product_name, current_stock, minimum_stock, unit))
        conn.commit()
    finally:
        conn.close()

def update_inventory(product_name, quantity_change):
    conn = get_connection()
    try:
        conn.execute('\n        UPDATE inventory\n\n        SET\n\n            current_stock = current_stock + ?,\n\n            updated_at = CURRENT_TIMESTAMP\n\n        WHERE product_name=?\n        ', (quantity_change, product_name))
        conn.commit()
    finally:
        conn.close()

def get_inventory():
    conn = get_connection()
    try:
        rows = conn.execute('\n        SELECT *\n\n        FROM inventory\n\n        WHERE is_active = 1\n\n        ORDER BY product_name\n        ').fetchall()
        return rows
    finally:
        conn.close()

def get_product_inventory(product_name):
    conn = get_connection()
    try:
        row = conn.execute('\n        SELECT *\n\n        FROM inventory\n\n        WHERE product_name=?\n        ', (product_name,)).fetchone()
        return row
    finally:
        conn.close()

def get_low_stock_items():
    conn = get_connection()
    try:
        rows = conn.execute('\n        SELECT *\n\n        FROM inventory\n\n        WHERE is_active = 1 AND minimum_stock > 0 AND current_stock <= minimum_stock\n        ').fetchall()
        return rows
    finally:
        conn.close()

def log_inventory_audit(product_name, action_type, source, user_phone=None, old_stock=None, new_stock=None, old_unit=None, new_unit=None, old_minimum=None, new_minimum=None, notes=None):
    """Log inventory changes to audit trail."""
    conn = get_connection()
    try:
        conn.execute('''
        INSERT INTO inventory_audit_log (
            product_name, action_type, old_stock, new_stock,
            old_unit, new_unit, old_minimum, new_minimum,
            source, user_phone, notes
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', (product_name, action_type, old_stock, new_stock, old_unit, new_unit, old_minimum, new_minimum, source, user_phone, notes))
        conn.commit()
    finally:
        conn.close()

def get_inventory_audit_log(product_name=None, limit=50):
    """Retrieve inventory audit log entries."""
    conn = get_connection()
    try:
        if product_name:
            rows = conn.execute('''
            SELECT * FROM inventory_audit_log
            WHERE product_name = ?
            ORDER BY created_at DESC
            LIMIT ?
            ''', (product_name, limit)).fetchall()
        else:
            rows = conn.execute('''
            SELECT * FROM inventory_audit_log
            ORDER BY created_at DESC
            LIMIT ?
            ''', (limit,)).fetchall()
        return rows
    finally:
        conn.close()

def delete_inventory(product_name):
    """Soft-delete an inventory item (mark as inactive, preserve history)."""
    conn = get_connection()
    try:
        conn.execute('''
        UPDATE inventory
        SET is_active = 0, updated_at = CURRENT_TIMESTAMP
        WHERE product_name = ?
        ''', (product_name,))
        conn.commit()
    finally:
        conn.close()

def update_purchase_order_status(purchase_order_id, target_status):
    conn = get_connection()
    try:
        cursor = conn.cursor()
        cursor.execute("SELECT status FROM purchase_orders WHERE id=?", (purchase_order_id,))
        row = cursor.fetchone()
        if not row:
            raise ValueError(f"Purchase order with ID {purchase_order_id} does not exist.")
        
        current_status = row[0]
        
        # Validate transition using the state machine
        from ai.procurement.state_machine import PurchaseOrderStateMachine
        PurchaseOrderStateMachine.validate_transition(current_status, target_status)
        
        cursor.execute("UPDATE purchase_orders SET status=? WHERE id=?", (target_status, purchase_order_id))
        if target_status.upper() == 'APPROVED':
            create_delivery_and_incoming_records_txn(cursor, purchase_order_id)
        conn.commit()
    finally:
        conn.close()

def approve_purchase_order(purchase_order_id):
    update_purchase_order_status(purchase_order_id, 'APPROVED')

def get_latest_purchase_order():
    conn = get_connection()
    try:
        row = conn.execute('\n        SELECT\n\n            id,\n            supplier,\n            status,\n            total_amount,\n            created_at\n\n        FROM purchase_orders\n\n        ORDER BY id DESC\n\n        LIMIT 1\n        ').fetchone()
        return row
    finally:
        conn.close()

def reject_latest_purchase_order():
    """
    Rejects the most recent DRAFT purchase order.

    Returns:
        {
            "purchase_order_id": ...,
            "supplier": ...
        }

    or None if no draft exists.
    """
    conn = get_connection()
    try:
        cursor = conn.cursor()
        cursor.execute("\n        SELECT id, supplier\n        FROM purchase_orders\n        WHERE status = 'DRAFT'\n        ORDER BY id DESC\n        LIMIT 1\n        ")
        row = cursor.fetchone()
        if row is None:
            return None
        purchase_order_id = row[0]
        supplier = row[1]
    finally:
        conn.close()

    update_purchase_order_status(purchase_order_id, 'REJECTED')
    return {'purchase_order_id': purchase_order_id, 'supplier': supplier}

def get_latest_draft_purchase_order():
    conn = get_connection()
    try:
        cursor = conn.cursor()
        cursor.execute("\n        SELECT id, supplier, total_amount\n        FROM purchase_orders\n        WHERE status='DRAFT'\n        ORDER BY id DESC\n        LIMIT 1\n    ")
        row = cursor.fetchone()
        return row
    finally:
        conn.close()

def get_purchase_order_items(purchase_order_id):
    conn = get_connection()
    try:
        cursor = conn.cursor()
        cursor.execute('\n        SELECT\n            product,\n            quantity,\n            unit,\n            estimated_price,\n            subtotal\n        FROM purchase_order_items\n        WHERE purchase_order_id=?\n    ', (purchase_order_id,))
        rows = cursor.fetchall()
        return rows
    finally:
        conn.close()

def update_purchase_order_item(purchase_order_id, product, quantity, subtotal):
    conn = get_connection()
    try:
        cursor = conn.cursor()
        cursor.execute('\n        UPDATE purchase_order_items\n        SET quantity=?,\n            subtotal=?\n        WHERE purchase_order_id=?\n        AND LOWER(product)=LOWER(?)\n        ', (quantity, subtotal, purchase_order_id, product))
        conn.commit()
    finally:
        conn.close()

def delete_purchase_order_item(purchase_order_id, product):
    conn = get_connection()
    try:
        cursor = conn.cursor()
        cursor.execute('\n        DELETE FROM purchase_order_items\n        WHERE purchase_order_id=?\n        AND LOWER(product)=LOWER(?)\n        ', (purchase_order_id, product))
        conn.commit()
    finally:
        conn.close()

def update_purchase_order_total(purchase_order_id, total):
    conn = get_connection()
    try:
        cursor = conn.cursor()
        cursor.execute('\n        UPDATE purchase_orders\n        SET total_amount=?\n        WHERE id=?\n    ', (total, purchase_order_id))
        conn.commit()
    finally:
        conn.close()

def get_purchase_order_items_by_order(purchase_order_id):
    conn = get_connection()
    try:
        cursor = conn.cursor()
        cursor.execute('\n        SELECT\n            product,\n            quantity,\n            unit,\n            estimated_price,\n            subtotal\n        FROM purchase_order_items\n        WHERE purchase_order_id=?\n    ', (purchase_order_id,))
        rows = cursor.fetchall()
        return rows
    finally:
        conn.close()



def get_top_selling_products(limit=10):
    conn = get_connection()
    try:
        cursor = conn.cursor()
        cursor.execute('\n        SELECT\n            product,\n            SUM(quantity) as qty\n        FROM order_history\n        GROUP BY product\n        ORDER BY qty DESC\n        LIMIT ?\n    ', (limit,))
        rows = cursor.fetchall()
        return rows
    finally:
        conn.close()

def get_all_purchase_invoices():
    conn = get_connection()
    try:
        cursor = conn.cursor()
        cursor.execute('\n        SELECT *\n        FROM purchase_invoices\n        ORDER BY id DESC\n    ')
        rows = cursor.fetchall()
        return rows
    finally:
        conn.close()

def create_delivery_and_incoming_records_txn(cursor, purchase_order_id):
    # 1. Fetch PO supplier and items
    cursor.execute("SELECT supplier FROM purchase_orders WHERE id=?", (purchase_order_id,))
    po_row = cursor.fetchone()
    if not po_row:
        return
    supplier = po_row[0]
    
    cursor.execute("SELECT product, quantity, unit FROM purchase_order_items WHERE purchase_order_id=?", (purchase_order_id,))
    items = cursor.fetchall()
    
    # 2. Add to expected_deliveries
    from datetime import datetime, timedelta
    expected_date = (datetime.now() + timedelta(days=1)).strftime("%Y-%m-%d") # default to tomorrow
    
    cursor.execute("""
        INSERT INTO expected_deliveries (purchase_order_id, supplier, expected_date, status)
        VALUES (?, ?, ?, 'PENDING')
    """, (purchase_order_id, supplier, expected_date))
    
    # 3. Add items to incoming_inventory
    for product, quantity, unit in items:
        cursor.execute("""
            INSERT INTO incoming_inventory (purchase_order_id, product, quantity, unit, expected_date, received)
            VALUES (?, ?, ?, ?, ?, 0)
        """, (purchase_order_id, product, quantity, unit, expected_date))

def get_open_purchase_order_by_supplier(supplier_name):
    conn = get_connection()
    try:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT id, status, supplier 
            FROM purchase_orders 
            WHERE LOWER(supplier) = LOWER(?) 
            AND status NOT IN ('CLOSED', 'REJECTED', 'CANCELLED')
            ORDER BY id DESC 
            LIMIT 1
        """, (supplier_name,))
        return cursor.fetchone()
    finally:
        conn.close()

def get_incoming_inventory_for_po(purchase_order_id):
    conn = get_connection()
    try:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT id, product, quantity, unit, received, received_quantity 
            FROM incoming_inventory 
            WHERE purchase_order_id = ?
        """, (purchase_order_id,))
        return cursor.fetchall()
    finally:
        conn.close()

def update_incoming_inventory_item(item_id, received_qty, received_date):
    conn = get_connection()
    try:
        cursor = conn.cursor()
        cursor.execute("""
            UPDATE incoming_inventory 
            SET received = 1,
                received_date = ?,
                received_quantity = ?
            WHERE id = ?
        """, (received_date, received_qty, item_id))
        conn.commit()
    finally:
        conn.close()

def mark_delivery_delivered(purchase_order_id):
    conn = get_connection()
    try:
        cursor = conn.cursor()
        cursor.execute("""
            UPDATE expected_deliveries
            SET status = 'DELIVERED'
            WHERE purchase_order_id = ?
        """, (purchase_order_id,))
        conn.commit()
    finally:
        conn.close()

def add_to_inventory_stock(product_name, quantity, unit):
    conn = get_connection()
    try:
        cursor = conn.cursor()
        cursor.execute("SELECT current_stock FROM inventory WHERE LOWER(product_name)=LOWER(?)", (product_name,))
        row = cursor.fetchone()
        if row:
            cursor.execute("""
                UPDATE inventory 
                SET current_stock = current_stock + ?, 
                    updated_at = CURRENT_TIMESTAMP 
                WHERE LOWER(product_name)=LOWER(?)
            """, (quantity, product_name))
        else:
            cursor.execute("""
                INSERT INTO inventory (product_name, current_stock, minimum_stock, unit) 
                VALUES (?, ?, 0.0, ?)
            """, (product_name, quantity, unit))
        conn.commit()
    finally:
        conn.close()

def transition_po_to_received_and_updated(purchase_order_id):
    conn = get_connection()
    try:
        cursor = conn.cursor()
        cursor.execute("SELECT status FROM purchase_orders WHERE id=?", (purchase_order_id,))
        row = cursor.fetchone()
        if not row:
            raise ValueError(f"Purchase order with ID {purchase_order_id} does not exist.")
        status = row[0].upper()
    finally:
        conn.close()
        
    # Apply sequential valid transitions
    if status == 'APPROVED':
        update_purchase_order_status(purchase_order_id, 'ORDERED')
        status = 'ORDERED'
    if status == 'ORDERED':
        update_purchase_order_status(purchase_order_id, 'SHIPPED')
        status = 'SHIPPED'
    if status == 'SHIPPED':
        update_purchase_order_status(purchase_order_id, 'RECEIVED')
        status = 'RECEIVED'
    if status == 'RECEIVED':
        update_purchase_order_status(purchase_order_id, 'INVENTORY_UPDATED')

def get_incoming_non_received_inventory_item(product_name):
    conn = get_connection()
    try:
        from datetime import datetime
        cursor = conn.cursor()
        today = datetime.now().strftime("%Y-%m-%d")
        cursor.execute("""
            SELECT expected_date 
            FROM incoming_inventory 
            WHERE LOWER(product) = LOWER(?) 
            AND received = 0 
            AND expected_date >= ?
            ORDER BY expected_date ASC
            LIMIT 1
        """, (product_name, today))
        row = cursor.fetchone()
        return row[0] if row else None
    finally:
        conn.close()

def get_base_product(description):
    return description.lower().strip()

def recalculate_product_memory(product_name):
    base_prod = get_base_product(product_name)
    
    conn = get_connection()
    try:
        cursor = conn.cursor()
        
        # 1. Fetch existing manual overrides
        cursor.execute("""
            SELECT preferred_brand_source, preferred_supplier_source, preferred_brand, preferred_supplier 
            FROM product_memory 
            WHERE product_name = ?
        """, (base_prod,))
        existing = cursor.fetchone()
        
        brand_src = 'AUTO'
        supplier_src = 'AUTO'
        pref_brand = None
        pref_supplier = None
        
        if existing:
            brand_src = existing[0]
            supplier_src = existing[1]
            pref_brand = existing[2]
            pref_supplier = existing[3]
            
        # 2. Fetch all historical purchases containing the base product keyword in purchase_items
        cursor.execute("""
            SELECT pi.invoice_date, pi.supplier, pit.quantity
            FROM purchase_items pit
            JOIN purchase_invoices pi ON pit.invoice_id = pi.id
            WHERE LOWER(pit.product) LIKE ?
            ORDER BY pi.invoice_date ASC
        """, (f"%{base_prod}%",))
        rows = cursor.fetchall()
        
        # 3. Calculate statistics
        num_purchases = len(rows)
        
        # Confidence Rules:
        if num_purchases == 0:
            confidence = 'NONE'
        elif num_purchases in (1, 2):
            confidence = 'LOW'
        else:
            confidence = 'HIGH'
            
        # Last Purchase Date
        last_purchase_date = None
        if num_purchases > 0:
            last_purchase_date = rows[-1][0] # invoice_date is YYYY-MM-DD
            
        # Average Reorder Interval
        avg_reorder_interval = None
        from datetime import datetime
        dates = []
        for r in rows:
            try:
                date_part = r[0].split()[0]
                d = datetime.strptime(date_part, "%Y-%m-%d")
                dates.append(d)
            except Exception:
                pass
        dates = sorted(list(set(dates)))
        if len(dates) >= 3:
            intervals = [(dates[i] - dates[i-1]).days for i in range(1, len(dates))]
            avg_reorder_interval = sum(intervals) / len(intervals)
            
        # Preferred Brand: DO NOT auto-detect. Keep existing pref_brand if MANUAL, otherwise set to None.
        if brand_src != 'MANUAL':
            pref_brand = None
                
        # Preferred Supplier (only compute if AUTO)
        if supplier_src == 'AUTO':
            suppliers = [r[1] for r in rows if r[1]]
            if suppliers:
                from collections import Counter
                pref_supplier = Counter(suppliers).most_common(1)[0][0]
            else:
                pref_supplier = None
                
        # 4. Usual Day of Week Calculation
        usual_day = None
        usual_day_conf = 'NONE'
        if num_purchases > 0:
            days = []
            for r in rows:
                try:
                    date_part = r[0].split()[0]
                    d = datetime.strptime(date_part, "%Y-%m-%d")
                    days.append(d.strftime("%A"))
                except Exception:
                    pass
            if days:
                from collections import Counter
                day_counts = Counter(days)
                mode_day, mode_count = day_counts.most_common(1)[0]
                total_valid_days = len(days)
                
                # Minimum sample size: 5 purchases, mode frequency >= 60%
                if total_valid_days >= 5 and (mode_count / total_valid_days) >= 0.60:
                    usual_day = mode_day
                    usual_day_conf = 'HIGH'
                elif total_valid_days >= 1:
                    usual_day = None
                    usual_day_conf = 'LOW'
                    
        # 5. Seasonal Spikes Calculation
        seasonal_spikes = None
        if num_purchases > 0:
            year_months = set()
            for r in rows:
                try:
                    date_part = r[0].split()[0]
                    year_months.add(date_part[:7])
                except Exception:
                    pass
            # 24-month rule threshold
            if len(year_months) >= 24:
                month_quantities = {}
                all_quantities = []
                for r in rows:
                    try:
                        date_part = r[0].split()[0]
                        m = int(date_part[5:7])
                        qty = float(r[2])
                        if m not in month_quantities:
                            month_quantities[m] = []
                        month_quantities[m].append(qty)
                        all_quantities.append(qty)
                    except Exception:
                        pass
                
                overall_avg_qty = sum(all_quantities) / len(all_quantities) if all_quantities else 0.0
                peak_months = []
                for m in range(1, 13):
                    q_list = month_quantities.get(m, [])
                    years_for_month = set()
                    for r in rows:
                        try:
                            date_part = r[0].split()[0]
                            if int(date_part[5:7]) == m:
                                years_for_month.add(date_part[:4])
                        except Exception:
                            pass
                    if len(years_for_month) >= 2 and q_list:
                        month_avg = sum(q_list) / len(q_list)
                        if month_avg >= 1.5 * overall_avg_qty:
                            import calendar
                            peak_months.append(calendar.month_name[m])
                if peak_months:
                    import json
                    seasonal_spikes = json.dumps(peak_months)
                    
        # 6. Insert or Update product_memory using OR REPLACE or ON CONFLICT
        cursor.execute("""
            INSERT INTO product_memory (
                product_name, preferred_brand, preferred_brand_source,
                preferred_supplier, preferred_supplier_source,
                avg_reorder_interval, last_purchase_date, confidence_level,
                usual_day_of_week, usual_day_of_week_confidence, seasonal_spikes, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(product_name) DO UPDATE SET
                preferred_brand = excluded.preferred_brand,
                preferred_brand_source = excluded.preferred_brand_source,
                preferred_supplier = excluded.preferred_supplier,
                preferred_supplier_source = excluded.preferred_supplier_source,
                avg_reorder_interval = excluded.avg_reorder_interval,
                last_purchase_date = excluded.last_purchase_date,
                confidence_level = excluded.confidence_level,
                usual_day_of_week = excluded.usual_day_of_week,
                usual_day_of_week_confidence = excluded.usual_day_of_week_confidence,
                seasonal_spikes = excluded.seasonal_spikes,
                updated_at = CURRENT_TIMESTAMP
        """, (base_prod, pref_brand, brand_src, pref_supplier, supplier_src, avg_reorder_interval, last_purchase_date, confidence, usual_day, usual_day_conf, seasonal_spikes))
        
        conn.commit()
    finally:
        conn.close()

def set_manual_product_preference(product_name, preferred_brand=None, preferred_supplier=None):
    base_prod = get_base_product(product_name)
    conn = get_connection()
    try:
        cursor = conn.cursor()
        cursor.execute("SELECT product_name FROM product_memory WHERE product_name=?", (base_prod,))
        exists = cursor.fetchone()
        
        if exists:
            if preferred_brand is not None:
                cursor.execute("""
                    UPDATE product_memory 
                    SET preferred_brand = ?, preferred_brand_source = 'MANUAL', updated_at = CURRENT_TIMESTAMP
                    WHERE product_name = ?
                """, (preferred_brand, base_prod))
            if preferred_supplier is not None:
                cursor.execute("""
                    UPDATE product_memory 
                    SET preferred_supplier = ?, preferred_supplier_source = 'MANUAL', updated_at = CURRENT_TIMESTAMP
                    WHERE product_name = ?
                """, (preferred_supplier, base_prod))
        else:
            brand_val = preferred_brand
            brand_src = 'MANUAL' if preferred_brand is not None else 'AUTO'
            supplier_val = preferred_supplier
            supplier_src = 'MANUAL' if preferred_supplier is not None else 'AUTO'
            
            cursor.execute("""
                INSERT INTO product_memory (
                    product_name, preferred_brand, preferred_brand_source, 
                    preferred_supplier, preferred_supplier_source, confidence_level
                ) VALUES (?, ?, ?, ?, ?, 'NONE')
            """, (base_prod, brand_val, brand_src, supplier_val, supplier_src))
        conn.commit()
    finally:
        conn.close()
    
    # Recalculate other non-manual stats after update
    recalculate_product_memory(base_prod)

def get_product_memory(product_name):
    base_prod = get_base_product(product_name)
    conn = get_connection()
    try:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT preferred_brand, preferred_brand_source,
                   preferred_supplier, preferred_supplier_source,
                   avg_reorder_interval, last_purchase_date, confidence_level,
                   usual_day_of_week, usual_day_of_week_confidence, seasonal_spikes
            FROM product_memory
            WHERE product_name = ?
        """, (base_prod,))
        row = cursor.fetchone()
        if row:
            return {
                'preferred_brand': row[0],
                'preferred_brand_source': row[1],
                'preferred_supplier': row[2],
                'preferred_supplier_source': row[3],
                'avg_reorder_interval': row[4],
                'last_purchase_date': row[5],
                'confidence_level': row[6],
                'usual_day_of_week': row[7],
                'usual_day_of_week_confidence': row[8],
                'seasonal_spikes': row[9]
            }
        return None
    finally:
        conn.close()

def recalculate_supplier_reliability(supplier_name):
    if not supplier_name:
        return
    
    conn = get_connection()
    try:
        cursor = conn.cursor()
        
        cursor.execute("""
            SELECT id FROM purchase_orders
            WHERE supplier = ? AND status IN ('INVENTORY_UPDATED', 'RECEIVED')
        """, (supplier_name,))
        po_rows = cursor.fetchall()
        po_ids = [row[0] for row in po_rows]
        
        total_deliveries = len(po_ids)
        
        if total_deliveries == 0:
            confidence = 'NONE'
        elif total_deliveries in (1, 2):
            confidence = 'LOW'
        else:
            confidence = 'HIGH'
            
        if total_deliveries == 0:
            cursor.execute("""
                INSERT INTO supplier_reliability (
                    supplier_name, total_deliveries, on_time_deliveries,
                    accuracy_rate, avg_delay_days, quantity_accuracy_rate,
                    confidence_level, updated_at
                ) VALUES (?, 0, 0, NULL, NULL, NULL, 'NONE', CURRENT_TIMESTAMP)
                ON CONFLICT(supplier_name) DO UPDATE SET
                    total_deliveries = 0,
                    on_time_deliveries = 0,
                    accuracy_rate = NULL,
                    avg_delay_days = NULL,
                    quantity_accuracy_rate = NULL,
                    confidence_level = 'NONE',
                    updated_at = CURRENT_TIMESTAMP
            """, (supplier_name,))
            conn.commit()
            return
            
        on_time_deliveries = 0
        total_delay_days = 0.0
        late_deliveries_count = 0
        
        total_expected_items = 0
        fully_received_items = 0
        
        from datetime import datetime
        for po_id in po_ids:
            cursor.execute("SELECT expected_date FROM expected_deliveries WHERE purchase_order_id = ?", (po_id,))
            exp_row = cursor.fetchone()
            if exp_row and exp_row[0]:
                expected_str = exp_row[0].split()[0]
                
                cursor.execute("""
                    SELECT MAX(received_date) FROM incoming_inventory
                    WHERE purchase_order_id = ? AND received = 1
                """, (po_id,))
                rec_row = cursor.fetchone()
                if rec_row and rec_row[0]:
                    received_str = rec_row[0].split()[0]
                    
                    try:
                        exp_date = datetime.strptime(expected_str, "%Y-%m-%d")
                        rec_date = datetime.strptime(received_str, "%Y-%m-%d")
                        delay = (rec_date - exp_date).days
                        
                        if delay <= 0:
                            on_time_deliveries += 1
                        else:
                            total_delay_days += delay
                            late_deliveries_count += 1
                    except Exception as e:
                        print(f"Error parsing dates for PO {po_id}: {e}")
                        
            cursor.execute("""
                SELECT quantity, received_quantity FROM incoming_inventory
                WHERE purchase_order_id = ?
            """, (po_id,))
            item_rows = cursor.fetchall()
            for ordered_qty, rec_qty in item_rows:
                total_expected_items += 1
                rec_qty_val = rec_qty if rec_qty is not None else 0.0
                if rec_qty_val >= ordered_qty:
                    fully_received_items += 1
                    
        accuracy_rate = (on_time_deliveries / total_deliveries) * 100.0
        avg_delay_days = (total_delay_days / late_deliveries_count) if late_deliveries_count > 0 else 0.0
        quantity_accuracy_rate = (fully_received_items / total_expected_items * 100.0) if total_expected_items > 0 else 100.0
        
        stats_accuracy_rate = accuracy_rate if confidence == 'HIGH' else None
        stats_avg_delay_days = avg_delay_days if confidence == 'HIGH' else None
        stats_quantity_accuracy_rate = quantity_accuracy_rate if confidence == 'HIGH' else None
        
        cursor.execute("""
            INSERT INTO supplier_reliability (
                supplier_name, total_deliveries, on_time_deliveries,
                accuracy_rate, avg_delay_days, quantity_accuracy_rate,
                confidence_level, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(supplier_name) DO UPDATE SET
                total_deliveries = excluded.total_deliveries,
                on_time_deliveries = excluded.on_time_deliveries,
                accuracy_rate = excluded.accuracy_rate,
                avg_delay_days = excluded.avg_delay_days,
                quantity_accuracy_rate = excluded.quantity_accuracy_rate,
                confidence_level = excluded.confidence_level,
                updated_at = CURRENT_TIMESTAMP
        """, (supplier_name, total_deliveries, on_time_deliveries, stats_accuracy_rate, stats_avg_delay_days, stats_quantity_accuracy_rate, confidence))
        
        conn.commit()
    finally:
        conn.close()

def get_supplier_reliability(supplier_name):
    conn = get_connection()
    try:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT total_deliveries, on_time_deliveries, accuracy_rate, avg_delay_days, quantity_accuracy_rate, confidence_level
            FROM supplier_reliability
            WHERE supplier_name = ?
        """, (supplier_name,))
        row = cursor.fetchone()
        if row:
            return {
                'total_deliveries': row[0],
                'on_time_deliveries': row[1],
                'accuracy_rate': row[2],
                'avg_delay_days': row[3],
                'quantity_accuracy_rate': row[4],
                'confidence_level': row[5]
            }
        return None
    finally:
        conn.close()

def get_supplier_leaderboard():
    conn = get_connection()
    try:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT supplier_name, accuracy_rate, quantity_accuracy_rate, confidence_level
            FROM supplier_reliability
            ORDER BY accuracy_rate DESC, quantity_accuracy_rate DESC
        """)
        rows = cursor.fetchall()
        return [
            {
                'supplier_name': r[0],
                'accuracy_rate': r[1],
                'quantity_accuracy_rate': r[2],
                'confidence_level': r[3]
            } for r in rows
        ]
    finally:
        conn.close()

def save_recipe(dish_name, ingredients):
    """
    Saves a recipe for a dish name. Deletes any existing ingredients for the dish first
    to ensure clean replacement.
    ingredients: list of dicts: [{'ingredient_name': str, 'quantity_per_unit': float, 'unit': str}]
    """
    conn = get_connection()
    try:
        cursor = conn.cursor()
        cursor.execute("DELETE FROM recipes WHERE LOWER(dish_name) = ?", (dish_name.lower().strip(),))
        for ing in ingredients:
            norm_name = get_base_product(ing['ingredient_name'])
            cursor.execute("""
                INSERT INTO recipes (dish_name, ingredient_name, quantity_per_unit, unit)
                VALUES (?, ?, ?, ?)
            """, (dish_name.strip(), norm_name, ing['quantity_per_unit'], ing['unit'].strip()))
        conn.commit()
    finally:
        conn.close()

def get_recipe(dish_name):
    conn = get_connection()
    try:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT ingredient_name, quantity_per_unit, unit
            FROM recipes
            WHERE LOWER(dish_name) = ?
        """, (dish_name.lower().strip(),))
        rows = cursor.fetchall()
        return [
            {
                'ingredient_name': r[0],
                'quantity_per_unit': r[1],
                'unit': r[2]
            } for r in rows
        ]
    finally:
        conn.close()

def get_all_recipes():
    """
    Retrieves all recipes grouped by dish name.
    """
    conn = get_connection()
    try:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT dish_name, ingredient_name, quantity_per_unit, unit 
            FROM recipes 
            ORDER BY dish_name
        """)
        rows = cursor.fetchall()
        recipes = {}
        for row in rows:
            dish, ing, qty, unit = row
            if dish not in recipes:
                recipes[dish] = []
            recipes[dish].append({
                'ingredient_name': ing,
                'quantity_per_unit': qty,
                'unit': unit
            })
        return recipes
    finally:
        conn.close()

def delete_recipe(dish_name):
    """
    Deletes all recipe rows for the specified dish.
    """
    conn = get_connection()
    try:
        cursor = conn.cursor()
        cursor.execute("DELETE FROM recipes WHERE LOWER(dish_name) = ?", (dish_name.lower().strip(),))
        conn.commit()
    finally:
        conn.close()

def save_sales_bill(bill_number, bill_date, total_amount, items, status='PENDING_CONFIRMATION'):
    """
    Saves a sales bill and its items.
    items: list of dicts: [{'dish_name': str, 'quantity': int, 'unit_price': float, 'total_price': float}]
    """
    conn = get_connection()
    try:
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO sales_bills (bill_number, bill_date, total_amount, status)
            VALUES (?, ?, ?, ?)
        """, (bill_number, bill_date, total_amount, status))
        bill_id = cursor.lastrowid
        
        for item in items:
            cursor.execute("""
                INSERT INTO sales_bill_items (bill_id, dish_name, quantity, unit_price, total_price)
                VALUES (?, ?, ?, ?, ?)
            """, (bill_id, item['dish_name'].strip(), item['quantity'], item.get('unit_price'), item.get('total_price')))
        conn.commit()
        return bill_id
    finally:
        conn.close()

def confirm_sales_bill(bill_id):
    """
    Confirms a sales bill and multiplies quantities sold by recipe ingredients
    to populate the product_consumption table.
    """
    conn = get_connection()
    try:
        cursor = conn.cursor()
        cursor.execute("SELECT status, bill_date FROM sales_bills WHERE id = ?", (bill_id,))
        row = cursor.fetchone()
        if not row:
            return False
        status, bill_date = row
        if status == 'CONFIRMED':
            return True
            
        cursor.execute("UPDATE sales_bills SET status = 'CONFIRMED' WHERE id = ?", (bill_id,))
        
        # Multiply dishes sold by recipes
        cursor.execute("""
            SELECT dish_name, quantity FROM sales_bill_items WHERE bill_id = ?
        """, (bill_id,))
        sold_items = cursor.fetchall()
        
        for dish_name, qty_sold in sold_items:
            cursor.execute("""
                SELECT ingredient_name, quantity_per_unit, unit FROM recipes WHERE LOWER(dish_name) = ?
            """, (dish_name.lower().strip(),))
            ingredients = cursor.fetchall()
            for ing_name, qty_per_unit, unit in ingredients:
                total_consumed = qty_sold * qty_per_unit
                cursor.execute("""
                    INSERT INTO product_consumption (product_name, consumed_quantity, unit, calculation_date, source_bill_id, status)
                    VALUES (?, ?, ?, ?, ?, 'PENDING')
                """, (ing_name, total_consumed, unit, bill_date, bill_id))
                
                cursor.execute("""
                    INSERT INTO pending_inventory_deductions (ingredient_name, estimated_quantity, unit, source_sales_bill_id)
                    VALUES (?, ?, ?, ?)
                """, (ing_name, total_consumed, unit, bill_id))
        conn.commit()
        return True
    finally:
        conn.close()

def get_weekly_consumption_summary():
    """
    Calculates total consumption per product over the last 7 days.
    """
    conn = get_connection()
    try:
        cursor = conn.cursor()
        from datetime import datetime, timedelta
        start_date = (datetime.now() - timedelta(days=6)).strftime('%Y-%m-%d')
        cursor.execute("""
            SELECT product_name, SUM(consumed_quantity), unit
            FROM product_consumption
            WHERE calculation_date >= ?
            GROUP BY product_name, unit
        """, (start_date,))
        rows = cursor.fetchall()
        return [
            {
                'product_name': r[0],
                'consumed_quantity': r[1],
                'unit': r[2]
            } for r in rows
        ]
    finally:
        conn.close()

def get_product_consumption_stats(product_name):
    """
    Returns consumption statistics for a specific product, including ADCR.
    """
    conn = get_connection()
    try:
        cursor = conn.cursor()
        # Check total distinct days of sales bills
        cursor.execute("SELECT COUNT(DISTINCT bill_date) FROM sales_bills WHERE status = 'CONFIRMED'")
        distinct_days = cursor.fetchone()[0]
        
        cursor.execute("""
            SELECT SUM(consumed_quantity), unit
            FROM product_consumption
            WHERE LOWER(product_name) = ?
        """, (product_name.lower().strip(),))
        row = cursor.fetchone()
        total_consumed = row[0] if row and row[0] is not None else 0.0
        unit = row[1] if row and row[1] is not None else ''
        
        adcr = None
        confidence = 'NONE'
        if distinct_days >= 3:
            # We compute ADCR over the last 7 days
            from datetime import datetime, timedelta
            start_date = (datetime.now() - timedelta(days=6)).strftime('%Y-%m-%d')
            cursor.execute("""
                SELECT SUM(consumed_quantity)
                FROM product_consumption
                WHERE LOWER(product_name) = ? AND calculation_date >= ?
            """, (product_name.lower().strip(), start_date))
            weekly_sum = cursor.fetchone()[0]
            weekly_sum_val = weekly_sum if weekly_sum is not None else 0.0
            adcr = weekly_sum_val / 7.0
            confidence = 'HIGH'
            
        return {
            'total_consumed': total_consumed,
            'unit': unit,
            'adcr': adcr,
            'adcr_confidence': confidence,
            'distinct_days': distinct_days
        }
    finally:
        conn.close()

def save_pending_document(phone, doc_type, payload):
    """
    Saves a pending document for a phone number. Marks any existing PENDING documents
    for that phone number as SUPERSEDED first.
    payload: dict or JSON string
    """
    conn = get_connection()
    try:
        cursor = conn.cursor()
        cursor.execute("""
            UPDATE pending_documents
            SET status = 'SUPERSEDED'
            WHERE phone = ? AND status = 'PENDING'
        """, (phone,))
        
        import json
        payload_str = json.dumps(payload) if isinstance(payload, dict) else payload
        
        cursor.execute("""
            INSERT INTO pending_documents (phone, doc_type, payload, status)
            VALUES (?, ?, ?, 'PENDING')
        """, (phone, doc_type, payload_str))
        doc_id = cursor.lastrowid
        conn.commit()
        return doc_id
    finally:
        conn.close()

def get_latest_pending_document(phone):
    conn = get_connection()
    try:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT id, doc_type, payload, status
            FROM pending_documents
            WHERE phone = ? AND status = 'PENDING'
            ORDER BY id DESC LIMIT 1
        """, (phone,))
        row = cursor.fetchone()
        if row:
            import json
            try:
                payload = json.loads(row[2])
            except Exception:
                payload = row[2]
            return {
                'id': row[0],
                'doc_type': row[1],
                'payload': payload,
                'status': row[3]
            }
        return None
    finally:
        conn.close()

def get_all_pending_documents():
    conn = get_connection()
    try:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT id, phone, doc_type, payload, status, created_at
            FROM pending_documents
            WHERE status = 'PENDING'
            ORDER BY id DESC
        """)
        rows = cursor.fetchall()
        results = []
        for row in rows:
            import json
            try:
                payload = json.loads(row[3])
            except Exception:
                payload = row[3]
            results.append({
                'id': row[0],
                'phone': row[1],
                'doc_type': row[2],
                'payload': payload,
                'status': row[4],
                'created_at': row[5]
            })
        return results
    finally:
        conn.close()

def get_pending_document_by_id(doc_id):
    conn = get_connection()
    try:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT id, phone, doc_type, payload, status, created_at
            FROM pending_documents
            WHERE id = ?
        """, (doc_id,))
        row = cursor.fetchone()
        if row:
            import json
            try:
                payload = json.loads(row[3])
            except Exception:
                payload = row[3]
            return {
                'id': row[0],
                'phone': row[1],
                'doc_type': row[2],
                'payload': payload,
                'status': row[4],
                'created_at': row[5]
            }
        return None
    finally:
        conn.close()

def update_pending_document_status(doc_id, status):
    conn = get_connection()
    try:
        cursor = conn.cursor()
        cursor.execute("""
            UPDATE pending_documents
            SET status = ?
            WHERE id = ?
        """, (status, doc_id))
        conn.commit()
    finally:
        conn.close()

def get_pending_inventory_deductions():
    """
    Retrieves all pending inventory deductions with their source sales bill details and current stock levels.
    """
    conn = get_connection()
    try:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT 
                p.id,
                p.ingredient_name,
                p.estimated_quantity,
                p.unit,
                p.source_sales_bill_id,
                s.bill_number,
                s.bill_date,
                i.current_stock
            FROM pending_inventory_deductions p
            JOIN sales_bills s ON p.source_sales_bill_id = s.id
            LEFT JOIN inventory i ON LOWER(p.ingredient_name) = LOWER(i.product_name)
            WHERE p.status = 'PENDING'
            ORDER BY s.bill_date DESC, p.id ASC
        """)
        rows = cursor.fetchall()
        results = []
        for row in rows:
            results.append({
                'id': row[0],
                'ingredient_name': row[1],
                'estimated_quantity': row[2],
                'unit': row[3],
                'source_sales_bill_id': row[4],
                'bill_number': row[5],
                'bill_date': row[6],
                'current_stock': row[7]
            })
        return results
    finally:
        conn.close()

def approve_inventory_deduction(deduction_id):
    """
    Approve deduction: decrement inventory current_stock and update statuses to 'APPROVED'.
    Blocks approval if the ingredient is untracked.
    """
    conn = get_connection()
    try:
        cursor = conn.cursor()
        cursor.execute("SELECT ingredient_name, estimated_quantity, unit, source_sales_bill_id, status FROM pending_inventory_deductions WHERE id = ?", (deduction_id,))
        row = cursor.fetchone()
        if not row:
            return False, "Deduction not found."
        ing_name, qty, unit, bill_id, status = row
        if status != 'PENDING':
            return False, f"Deduction is already {status}."
        
        # Enforce that the ingredient must be tracked
        cursor.execute("SELECT 1 FROM inventory WHERE LOWER(product_name) = LOWER(?)", (ing_name,))
        exists = cursor.fetchone()
        if not exists:
            return False, f"Ingredient '{ing_name}' is not tracked in inventory. Add it to inventory first."
        
        # Perform the stock decrement
        cursor.execute("""
            UPDATE inventory
            SET current_stock = current_stock - ?,
                updated_at = CURRENT_TIMESTAMP
            WHERE LOWER(product_name) = LOWER(?)
        """, (qty, ing_name))
        
        # Update pending deduction status
        cursor.execute("UPDATE pending_inventory_deductions SET status = 'APPROVED' WHERE id = ?", (deduction_id,))
        
        # Update product consumption status
        cursor.execute("""
            UPDATE product_consumption 
            SET status = 'APPROVED' 
            WHERE LOWER(product_name) = LOWER(?) AND source_bill_id = ?
        """, (ing_name, bill_id))
        
        conn.commit()
        return True, "Deduction approved successfully."
    finally:
        conn.close()

def reject_inventory_deduction(deduction_id):
    """
    Reject deduction: do not touch stock, change status of deduction and consumption to 'REJECTED'.
    """
    conn = get_connection()
    try:
        cursor = conn.cursor()
        cursor.execute("SELECT ingredient_name, source_sales_bill_id, status FROM pending_inventory_deductions WHERE id = ?", (deduction_id,))
        row = cursor.fetchone()
        if not row:
            return False, "Deduction not found."
        ing_name, bill_id, status = row
        if status != 'PENDING':
            return False, f"Deduction is already {status}."
        
        # Update pending deduction status
        cursor.execute("UPDATE pending_inventory_deductions SET status = 'REJECTED' WHERE id = ?", (deduction_id,))
        
        # Update product consumption status
        cursor.execute("""
            UPDATE product_consumption 
            SET status = 'REJECTED' 
            WHERE LOWER(product_name) = LOWER(?) AND source_bill_id = ?
        """, (ing_name, bill_id))
        
        conn.commit()
        return True, "Deduction rejected successfully."
    finally:
        conn.close()

if __name__ == '__main__':
    init_db()
    print('Database initialized successfully.')