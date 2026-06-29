import asyncio

from ai.services.swiggy_service import SwiggyService


async def main():

    service = SwiggyService()

    products = await service.search_products(
        "Milk"
    )

    print()

    print(products[:3])


asyncio.run(main())