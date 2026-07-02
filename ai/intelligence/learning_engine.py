from ai.memory.restaurant_memory import restaurant_memory
from ai.memory.reflection_memory import reflection_memory

from ai.agents.reflection_agent import reflection_agent


class LearningEngine:

    async def learn(

        self,

        procurement_plan,

        purchased_items,

        checkout_result

    ):

        memory = restaurant_memory.get()

        reflection = await reflection_agent.execute(

            procurement_plan,

            purchased_items,

            checkout_result,

            memory

        )

        reflection_memory.add(

            decision=procurement_plan,

            outcome=checkout_result,

            lesson=reflection["lesson"]

        )

        return reflection


learning_engine = LearningEngine()