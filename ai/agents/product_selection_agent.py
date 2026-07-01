import asyncio

from ai.shopping.shopping_session import shopping_session
from ai.services.swiggy_service import SwiggyService


class ProductSelectionAgent:
    """
    Handles product selection during
    the shopping conversation.
    """

    def __init__(self):

        self.service = SwiggyService()

    async def execute(self, phone, message):

        session = shopping_session.get(phone)

        if not session:

            return {

                "message":

                    "No shopping session found."

            }

        # ----------------------------
        # Validate Choice
        # ----------------------------

        try:

            choice = int(message.strip()) - 1

        except ValueError:

            return {

                "message":

                    "Reply with a number."

            }

        options = session["options"]

        if choice < 0 or choice >= len(options):

            return {

                "message":

                    "Invalid choice."

            }

        # ----------------------------
        # Save Selected Product
        # ----------------------------

        shopping_session.select(

            phone,

            choice

        )

        # ----------------------------
        # Finished?
        # ----------------------------

        if shopping_session.finished(phone):

            session = shopping_session.end(phone)

            cart = await self.service.build_cart(
                session["selected"]
            )

            total = 0

            reply = []

            reply.append("🛒 Swiggy Cart Ready")
            reply.append("")

            for item in session["selected"]:

                subtotal = (
                    item["price"] *
                    item["quantity"]
                )

                total += subtotal

                reply.append(
                    f"• {item['displayName']}"
                )

                reply.append(
                    f"Qty : {item['quantity']}"
                )

                reply.append(
                    f"₹{subtotal}"
                )

                reply.append("")

            reply.append(
                f"Estimated Total : ₹{total}"
            )

            reply.append("")
            reply.append(
                "Reply YES to place the order."
            )

            return {

                "message": "\n".join(reply),

                "cart": cart
            }

        # ----------------------------
        # Search Next Item
        # ----------------------------

        item = shopping_session.current_item(phone)

        products = await self.service.search_products(

            item["name"]

        )

        shopping_session.set_options(

            phone,

            products

        )

        reply = []

        reply.append(

            f"Choose {item['name']}"

        )

        reply.append("")

        for i, product in enumerate(

            products[:5],

            start=1

        ):

            variant = product["variations"][0]

            reply.append(

                f"{i}. "

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