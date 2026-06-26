import sqlite3

DB_PATH = "database/orders.db"


def get_connection():

    return sqlite3.connect(DB_PATH)


def init_db():

    conn = get_connection()

    cursor = conn.cursor()

    # --------------------------------------------------
    # Orders
    # --------------------------------------------------

    cursor.execute("""
    CREATE TABLE IF NOT EXISTS orders(

        id INTEGER PRIMARY KEY AUTOINCREMENT,

        product_name TEXT,

        spin_id TEXT,

        quantity INTEGER,

        order_type TEXT,

        schedule_time TEXT,

        recurrence TEXT,

        status TEXT DEFAULT 'active'

    )
    """)

    # --------------------------------------------------
    # Pending Orders
    # --------------------------------------------------

    cursor.execute("""
    CREATE TABLE IF NOT EXISTS pending_orders(

        id INTEGER PRIMARY KEY AUTOINCREMENT,

        product_name TEXT,

        spin_id TEXT,

        quantity INTEGER,

        created_at TIMESTAMP
        DEFAULT CURRENT_TIMESTAMP,

        status TEXT DEFAULT
        'awaiting_confirmation'

    )
    """)

    # --------------------------------------------------
    # Order History
    # --------------------------------------------------

    cursor.execute("""
    CREATE TABLE IF NOT EXISTS order_history(

        id INTEGER PRIMARY KEY AUTOINCREMENT,

        product_name TEXT,

        quantity INTEGER,

        amount REAL,

        order_id TEXT,

        ordered_at TIMESTAMP
        DEFAULT CURRENT_TIMESTAMP

    )
    """)

    # --------------------------------------------------
    # Purchase Invoices
    # --------------------------------------------------

    cursor.execute("""
    CREATE TABLE IF NOT EXISTS purchase_invoices(

        id INTEGER PRIMARY KEY AUTOINCREMENT,

        supplier TEXT,

        invoice_number TEXT,

        invoice_date TEXT,

        total_amount REAL,

        created_at TIMESTAMP
        DEFAULT CURRENT_TIMESTAMP

    )
    """)

    # --------------------------------------------------
    # Purchase Items
    # --------------------------------------------------

    cursor.execute("""
    CREATE TABLE IF NOT EXISTS purchase_items(

        id INTEGER PRIMARY KEY AUTOINCREMENT,

        invoice_id INTEGER,

        product TEXT,

        quantity REAL,

        unit TEXT,

        unit_price REAL,

        total REAL,

        FOREIGN KEY(invoice_id)
        REFERENCES purchase_invoices(id)

    )
    """)

    # --------------------------------------------------
    # Product Price History
    # --------------------------------------------------

    cursor.execute("""
    CREATE TABLE IF NOT EXISTS product_price_history(

        id INTEGER PRIMARY KEY AUTOINCREMENT,

        product TEXT,

        supplier TEXT,

        price REAL,

        purchase_date TEXT

    )
    """)

    conn.commit()
    conn.close()


# ======================================================
# ORDERS
# ======================================================

def save_order(

    product_name,

    spin_id,

    quantity,

    order_type,

    schedule_time=None,

    recurrence=None

):

    conn = get_connection()

    conn.execute(

        """
        INSERT INTO orders(

            product_name,

            spin_id,

            quantity,

            order_type,

            schedule_time,

            recurrence

        )

        VALUES (?, ?, ?, ?, ?, ?)

        """,

        (

            product_name,

            spin_id,

            quantity,

            order_type,

            schedule_time,

            recurrence

        )

    )

    conn.commit()
    conn.close()


def get_orders():

    conn = get_connection()

    rows = conn.execute(

        """
        SELECT *
        FROM orders
        """

    ).fetchall()

    conn.close()

    return rows

# ======================================================
# PENDING ORDERS
# ======================================================

def save_pending_order(

    product_name,

    spin_id,

    quantity

):

    conn = get_connection()

    conn.execute(

        """

        INSERT INTO pending_orders(

            product_name,

            spin_id,

            quantity

        )

        VALUES (?, ?, ?)

        """,

        (

            product_name,

            spin_id,

            quantity

        )

    )

    conn.commit()

    conn.close()


def get_pending_orders():

    conn = get_connection()

    rows = conn.execute(

        """

        SELECT *

        FROM pending_orders

        WHERE status='awaiting_confirmation'

        """

    ).fetchall()

    conn.close()

    return rows


def pending_exists(

    product_name,

    spin_id

):

    conn = get_connection()

    row = conn.execute(

        """

        SELECT id

        FROM pending_orders

        WHERE product_name=?

        AND spin_id=?

        AND status='awaiting_confirmation'

        """,

        (

            product_name,

            spin_id

        )

    ).fetchone()

    conn.close()

    return row is not None


def mark_pending_completed(

    pending_id

):

    conn = get_connection()

    conn.execute(

        """

        UPDATE pending_orders

        SET status='completed'

        WHERE id=?

        """,

        (pending_id,)

    )

    conn.commit()

    conn.close()


# ======================================================
# ORDER HISTORY
# ======================================================

def save_order_history(

    product_name,

    quantity,

    amount,

    order_id

):

    conn = get_connection()

    conn.execute(

        """

        INSERT INTO order_history(

            product_name,

            quantity,

            amount,

            order_id

        )

        VALUES (?, ?, ?, ?)

        """,

        (

            product_name,

            quantity,

            amount,

            order_id

        )

    )

    conn.commit()

    conn.close()


def get_order_history():

    conn = get_connection()

    rows = conn.execute(

        """

        SELECT *

        FROM order_history

        ORDER BY id DESC

        """

    ).fetchall()

    conn.close()

    return rows


# ======================================================
# PURCHASE INVOICES
# ======================================================

def save_invoice(invoice):

    conn = get_connection()

    cursor = conn.cursor()

    cursor.execute(

        """

        INSERT INTO purchase_invoices(

            supplier,

            invoice_number,

            invoice_date,

            total_amount

        )

        VALUES (?, ?, ?, ?)

        """,

        (

            invoice["supplier"],

            invoice["invoice_number"],

            invoice["date"],

            invoice["total_amount"]

        )

    )

    invoice_id = cursor.lastrowid

    for item in invoice["items"]:

        cursor.execute(

            """

            INSERT INTO purchase_items(

                invoice_id,

                product,

                quantity,

                unit,

                unit_price,

                total

            )

            VALUES (?, ?, ?, ?, ?, ?)

            """,

            (

                invoice_id,

                item["product"],

                item["quantity"],

                item["unit"],

                item["unit_price"],

                item["total"]

            )

        )

        cursor.execute(

            """

            INSERT INTO product_price_history(

                product,

                supplier,

                price,

                purchase_date

            )

            VALUES (?, ?, ?, ?)

            """,

            (

                item["product"],

                invoice["supplier"],

                item["unit_price"],

                invoice["date"]

            )

        )

    conn.commit()

    conn.close()


def get_invoices():

    conn = get_connection()

    rows = conn.execute(

        """

        SELECT *

        FROM purchase_invoices

        ORDER BY id DESC

        """

    ).fetchall()

    conn.close()

    return rows


def get_invoice_items(invoice_id):

    conn = get_connection()

    rows = conn.execute(

        """

        SELECT *

        FROM purchase_items

        WHERE invoice_id=?

        """,

        (invoice_id,)

    ).fetchall()

    conn.close()

    return rows

# ======================================================
# PRICE HISTORY
# ======================================================

def get_price_history(product):

    conn = get_connection()

    rows = conn.execute(

        """
        SELECT

            supplier,

            price,

            purchase_date

        FROM product_price_history

        WHERE product=?

        ORDER BY purchase_date DESC
        """,

        (product,)

    ).fetchall()

    conn.close()

    return rows


def get_latest_price(product):

    conn = get_connection()

    row = conn.execute(

        """
        SELECT

            supplier,

            price,

            purchase_date

        FROM product_price_history

        WHERE product=?

        ORDER BY purchase_date DESC

        LIMIT 1
        """,

        (product,)

    ).fetchone()

    conn.close()

    return row


# ======================================================
# SUPPLIER ANALYTICS
# ======================================================

def get_supplier_prices(product):

    conn = get_connection()

    rows = conn.execute(

        """
        SELECT

            supplier,

            AVG(price)

        FROM product_price_history

        WHERE product=?

        GROUP BY supplier
        """,

        (product,)

    ).fetchall()

    conn.close()

    return rows


def get_cheapest_supplier(product):

    suppliers = get_supplier_prices(product)

    if not suppliers:

        return None

    return min(

        suppliers,

        key=lambda x: x[1]

    )


def get_top_suppliers():

    conn = get_connection()

    rows = conn.execute(

        """
        SELECT

            supplier,

            COUNT(*)

        FROM purchase_invoices

        GROUP BY supplier

        ORDER BY COUNT(*) DESC
        """

    ).fetchall()

    conn.close()

    return rows


def get_supplier_statistics():

    conn = get_connection()

    rows = conn.execute(

        """
        SELECT

            supplier,

            COUNT(*) AS invoices,

            SUM(total_amount)

        FROM purchase_invoices

        GROUP BY supplier

        ORDER BY SUM(total_amount) DESC
        """

    ).fetchall()

    conn.close()

    return rows


def get_total_spend_by_supplier(supplier):

    conn = get_connection()

    row = conn.execute(

        """
        SELECT

            SUM(total_amount)

        FROM purchase_invoices

        WHERE supplier=?
        """,

        (supplier,)

    ).fetchone()

    conn.close()

    return row[0] if row and row[0] else 0


# ======================================================
# PROCUREMENT ANALYTICS
# ======================================================

def get_monthly_spend():

    conn = get_connection()

    rows = conn.execute(

        """
        SELECT

            substr(invoice_date,1,7),

            SUM(total_amount)

        FROM purchase_invoices

        GROUP BY substr(invoice_date,1,7)

        ORDER BY substr(invoice_date,1,7)
        """

    ).fetchall()

    conn.close()

    return rows


def get_product_purchase_history(product):

    conn = get_connection()

    rows = conn.execute(

        """
        SELECT

            purchase_date,

            supplier,

            price

        FROM product_price_history

        WHERE product=?

        ORDER BY purchase_date
        """,

        (product,)

    ).fetchall()

    conn.close()

    return rows


def get_all_products():

    conn = get_connection()

    rows = conn.execute(

        """
        SELECT DISTINCT product

        FROM purchase_items

        ORDER BY product
        """

    ).fetchall()

    conn.close()

    return [

        row[0]

        for row in rows

    ]


# ======================================================
# DASHBOARD ANALYTICS
# ======================================================

def get_dashboard_stats():

    return {

        "orders":

            len(get_orders()),

        "pending":

            len(get_pending_orders()),

        "history":

            len(get_order_history()),

        "invoices":

            len(get_invoices()),

        "suppliers":

            len(get_top_suppliers()),

        "products":

            len(get_all_products())

    }


if __name__ == "__main__":

    init_db()

    print("Database initialized successfully.")