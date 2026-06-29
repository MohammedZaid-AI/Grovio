import asyncio

from integrations.swiggy.swiggy_mcp import SwiggyInstamart


class SwiggyService:
    """
    High-level wrapper around Swiggy MCP.

    All agents should use this service instead
    of directly calling SwiggyInstamart.
    """

    def __init__(self):

        self.client = None

    async def initialize(self):

        if self.client is None:

            self.client = await SwiggyInstamart().initialize()

        return self.client

    async def search_products(
        self,
        product_name
    ):

        client = await self.initialize()

        return await client.get_product_options(
            product_name
        )

    async def clear_cart(self):

        client = await self.initialize()

        return await client.clear_cart()

    async def get_cart(self):

        client = await self.initialize()

        return await client.get_cart()

    async def add_items(
        self,
        items
    ):

        client = await self.initialize()

        address_id = await client.get_address_id()

        return await client.update_cart(
            address_id,
            items
        )

    async def checkout(self):

        client = await self.initialize()

        address_id = await client.get_address_id()

        return await client.checkout(
            address_id
        )