from ai.memory.restaurant_memory import restaurant_memory


class MemoryTrainer:
    """
    Learns restaurant preferences from
    completed purchases.
    """

    def train(self, items):

        for item in items:

            product = item["displayName"]

            restaurant_memory.update_product(

                product

            )

            restaurant_memory.update_frequency(

                product

            )

            brand = product.split()[0]

            restaurant_memory.set_preferred_brand(

                product,

                brand

            )


memory_trainer = MemoryTrainer()