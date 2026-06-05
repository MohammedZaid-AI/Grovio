import sqlite3

DB_PATH = "database/orders.db"


def init_db():
    conn = sqlite3.connect(DB_PATH)

    conn.execute("""
    CREATE TABLE IF NOT EXISTS orders(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        item TEXT,
        quantity INTEGER,
        schedule_time TEXT,
        recurrence TEXT,
        status TEXT DEFAULT 'pending'
    )
    """)

    conn.commit()
    conn.close()


def save_order(item, quantity, schedule_time, recurrence=None):

    conn = sqlite3.connect(DB_PATH)

    conn.execute(
        """
        INSERT INTO orders(
            item,
            quantity,
            schedule_time,
            recurrence
        )
        VALUES (?, ?, ?, ?)
        """,
        (
            item,
            quantity,
            schedule_time,
            recurrence
        )
    )

    conn.commit()
    conn.close()


def get_orders():
    conn = sqlite3.connect(DB_PATH)

    rows = conn.execute(
        "SELECT * FROM orders"
    ).fetchall()

    conn.close()

    return rows