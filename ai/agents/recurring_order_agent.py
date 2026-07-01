from ai.shopping.orchestrator import ShoppingOrchestrator


class RecurringOrderAgent:

    def __init__(self):

        self.orchestrator = ShoppingOrchestrator()

    async def execute(

        self,

        phone

    ):

        return await self.orchestrator.start(

            phone=phone,

            source="recurring"

        )