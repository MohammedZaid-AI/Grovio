from db import *

po = get_latest_draft_purchase_order()

print(po)

print()

print(get_purchase_order_items(po[0]))