from db import *

save_inventory(
    "Milk",
    10,
    5,
    "packets"
)

save_inventory(
    "Bread",
    2,
    5,
    "loaves"
)

print("Inventory")
print(get_inventory())

print()

print("Low Stock")
print(get_low_stock_items())