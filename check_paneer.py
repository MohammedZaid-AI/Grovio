import sqlite3
conn = sqlite3.connect('database/orders.db')
print(conn.execute("SELECT * FROM inventory WHERE product_name='paneer'").fetchall())
conn.close()
