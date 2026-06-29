import re

from ai.shopping.shopping_session import shopping_session
from ai.services.swiggy_service import SwiggyService


class ManualOrderAgent:
    """
    Handles manual grocery ordering
    through WhatsApp.

    Example:

    Order
    5 milk
    2 butter
    """

    def __init__(self):

        self.service = SwiggyService()

    # ----------------------------------
    # Parse User Order
    # ----------------------------------

    def parse(self, text):

        items = []

        lines = text.splitlines()

        pattern = r"(\d+)\s+(.+)"

        for line in lines:

            line = line.strip()

            match = re.match(pattern, line)

            if match:

                quantity = int(match.group(1))

                name = match.group(2)

                items.append({

                    "name": name,

                    "quantity": quantity

                })

        return items

    # ----------------------------------
    # Start Shopping
    # ----------------------------------

    async def execute(

        self,

        phone,

        message

    ):

        items = self.parse(message)

        if not items:

            return {

                "message":

                    "I couldn't understand your order."

            }

        shopping_session.start(

            phone,

            items

        )

        first_item = shopping_session.next_item(

            phone

        )

        products = await self.service.search_products(

            first_item["name"]

        )

        if not products:

            return {

                "message":

                    f"No products found for {first_item['name']}"

            }

        session = shopping_session.get(phone)

        session["options"] = products

        reply = []

        reply.append(

            f"Choose {first_item['name']}"

        )

        reply.append("")

        for index, product in enumerate(

            products[:5],

            start=1

        ):

            variant = product["variations"][0]

            reply.append(

                f"{index}. "

                f"{product['displayName']} "

                f"({variant['quantityDescription']}) "

                f"₹{variant['price']['offerPrice']}"

            )

        reply.append("")

        reply.append(

            "Reply with 1-5."

        )

        return {

            "message":

                "\n".join(reply)

        }