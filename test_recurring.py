from db import save_order

save_order(
    product_name="Nandini Shubham Milk",
    spin_id="J4ZRFJNQRO",
    quantity=2,
    order_type="recurring",
    schedule_time="08:00",
    recurrence="Monday,Friday"
)

print(
    "Recurring order saved"
)