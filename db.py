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
        cursor.execute('\n    CREATE TABLE IF NOT EXISTS inventory(\n\n        id INTEGER PRIMARY KEY AUTOINCREMENT,\n\n        product_name TEXT UNIQUE,\n\n        current_stock REAL,\n\n        minimum_stock REAL,\n\n        unit TEXT,\n\n        updated_at TIMESTAMP\n        DEFAULT CURRENT_TIMESTAMP\n\n    )\n    ')
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
        rows = conn.execute('\n        SELECT *\n\n        FROM inventory\n\n        ORDER BY product_name\n        ').fetchall()
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
        rows = conn.execute('\n        SELECT *\n\n        FROM inventory\n\n        WHERE current_stock <= minimum_stock\n        ').fetchall()
        return rows
    finally:
        conn.close()

def approve_purchase_order(purchase_order_id):
    conn = get_connection()
    try:
        conn.execute("\n        UPDATE purchase_orders\n\n        SET status='APPROVED'\n\n        WHERE id=?\n        ", (purchase_order_id,))
        conn.commit()
    finally:
        conn.close()

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
            conn.close()
            return None
        purchase_order_id = row[0]
        supplier = row[1]
        cursor.execute("\n        UPDATE purchase_orders\n        SET status='REJECTED'\n        WHERE id=?\n        ", (purchase_order_id,))
        conn.commit()
        return {'purchase_order_id': purchase_order_id, 'supplier': supplier}
    finally:
        conn.close()

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
if __name__ == '__main__':
    init_db()
    print('Database initialized successfully.')