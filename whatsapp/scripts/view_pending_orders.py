from db import get_pending_orders

orders = get_pending_orders()

for order in orders:

    print(order)