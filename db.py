import sqlite3

DB_PATH = "database/orders.db"


def init_db():

    conn = sqlite3.connect(
        DB_PATH
    )

    conn.execute("""
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

    conn.execute("""
    CREATE TABLE IF NOT EXISTS pending_orders(

        id INTEGER PRIMARY KEY AUTOINCREMENT,

        product_name TEXT,

        spin_id TEXT,

        quantity INTEGER,

        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,

        status TEXT DEFAULT
        'awaiting_confirmation'
    )
    """)

    conn.commit()

    conn.close()


def save_order(

    product_name,

    spin_id,

    quantity,

    order_type,

    schedule_time=None,

    recurrence=None
):

    conn = sqlite3.connect(
        DB_PATH
    )

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

    conn = sqlite3.connect(
        DB_PATH
    )

    rows = conn.execute(
        "SELECT * FROM orders"
    ).fetchall()

    conn.close()

    return rows


def save_pending_order(

    product_name,

    spin_id,

    quantity
):

    conn = sqlite3.connect(
        DB_PATH
    )

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

    conn = sqlite3.connect(
        DB_PATH
    )

    rows = conn.execute(
        """
        SELECT *
        FROM pending_orders
        """
    ).fetchall()

    conn.close()

    return rows

def mark_pending_completed(
    pending_id
):

    conn = sqlite3.connect(
        DB_PATH
    )

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