from ai.tools.base_tool import BaseTool
from ai.services.swiggy_service import SwiggyService


class SearchProductsTool(BaseTool):

    @property
    def name(self):

        return "search_products"

    async def execute(

        self,

        query

    ):

        service = SwiggyService()

        return await service.search_products(

            query

        )