from ai.shopping.orchestrator import ShoppingOrchestrator


class ManualOrderAgent:
    """
    Handles manual grocery ordering.

    Example:

    Order groceries

    2 Milk
    1 Butter
    5 Coke
    """

    def __init__(self):

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

            phone=phone,

            source="manual",

            message=message

        )