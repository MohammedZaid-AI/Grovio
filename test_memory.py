from ai.memory import RestaurantMemory

memory = RestaurantMemory()

print()

print("Completed Orders")
print(memory.total_completed_orders())

print()

print("Pending Orders")
print(memory.total_pending_orders())

print()

print("Restaurant Spend")
print(memory.total_spend())

print()

print("Average Order")
print(memory.average_order_value())

print()

print("Favorite Purchase Day")
print(memory.favorite_purchase_day())

print()

print("Most Purchased")

for item in memory.most_purchased_products():

    print(item)