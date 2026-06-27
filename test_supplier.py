from db import *

product = "City Bakery Citi Sliced White Bread"

print("=" * 60)

print("PRICE HISTORY")
print(get_price_history(product))

print("=" * 60)

print("SUPPLIER PRICES")
print(get_supplier_prices(product))

print("=" * 60)

print("CHEAPEST SUPPLIER")
print(get_cheapest_supplier(product))

print("=" * 60)