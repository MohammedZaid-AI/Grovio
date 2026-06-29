from db import get_connection

conn = get_connection()

cursor = conn.cursor()

cursor.execute("""
SELECT name
FROM sqlite_master
WHERE type='table'
ORDER BY name
""")

for table in cursor.fetchall():

    print(table[0])

conn.close()