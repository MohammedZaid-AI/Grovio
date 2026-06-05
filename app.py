from agent import parse_order
from db import (
    init_db,
    save_order,
    get_orders
)

init_db()

text = input("Enter order: ")

data = parse_order(text)

print("\nParsed JSON:")
print(data)

recurrence = data.get("recurrence")

if isinstance(recurrence, list):
    recurrence = ",".join(recurrence)

save_order(
    data["item"],
    data["quantity"],
    data["schedule_time"],
    recurrence
)

print("\nOrder Saved")

print(get_orders())