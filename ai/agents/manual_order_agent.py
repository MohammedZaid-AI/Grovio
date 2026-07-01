from ai.shopping.shopping_session import shopping_session
from ai.services.swiggy_service import SwiggyService
from ai.shopping.orchestrator import ShoppingOrchestrator


class ManualOrderAgent:
    """
    Handles manual grocery ordering.

    Example:

    5 milk
    2 butter
    """

    def __init__(self):

        self.service = SwiggyService()

        self.orchestrator = ShoppingOrchestrator()

    # ----------------------------------
    # Execute
    # ----------------------------------

    async def execute(

        self,

        phone,

        message

    ):

        return await self.orchestrator.start(

            phone,

            source="manual",

            message=message

        )