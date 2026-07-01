import asyncio

print("1. Imports done")

from ai.agents.manual_order_agent import ManualOrderAgent
from ai.agents.product_selection_agent import ProductSelectionAgent

print("2. Agents imported")


async def main():

    print("3. Inside main")

    phone = "9999999999"

    manual = ManualOrderAgent()

    print("4. Manual agent created")

    selector = ProductSelectionAgent()

    print("5. Selector created")

    result = await manual.execute(
        phone,
        """
5 milk
2 butter
"""
    )

    print("6. Manual execute finished")

    print(result["message"])

    while True:

        reply = input("> ")

        result = await selector.execute(phone, reply)

        print(result["message"])

        if "cart" in result:

            print()

            confirm = input("YES / NO : ")

            if confirm.lower() == "yes":

                result = await selector.service.checkout()

                print(result)

            else:

                print("Order cancelled.")
            

asyncio.run(main())