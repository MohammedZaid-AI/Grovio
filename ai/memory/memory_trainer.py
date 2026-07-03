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

            import re
            tokens = product.split()
            brand = "Unknown"
            if tokens:
                for token in tokens:
                    if re.search(r"\d", token):
                        continue
                    if token.lower() in [
                        "kg", "g", "ml", "l", "ltr", "ltrs", "litre", "litres", 
                        "pc", "pcs", "pack", "packs", "no", "no.", "size", "wt", "vol"
                    ]:
                        continue
                    brand = token
                    break
                if brand == "Unknown" and tokens:
                    brand = tokens[0]

            restaurant_memory.set_preferred_brand(

                product,

                brand

            )


memory_trainer = MemoryTrainer()