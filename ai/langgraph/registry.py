from ai.agents.ai_coo import AICOO
from ai.intelligence.decision_engine import DecisionEngine


class AgentRegistry:

    """
    Central registry of all AI agents.

    The supervisor uses this registry
    to discover and execute agents.
    """

    def __init__(self):

        self.agents = {

            "coo": AICOO(),

            "decision": DecisionEngine(),

            # Future agents
            # "supplier": SupplierAgent(),
            # "procurement": ProcurementAgent(),
            # "analytics": AnalyticsAgent(),
            # "inventory": InventoryAgent(),
            # "invoice": InvoiceAgent(),

        }

    # -----------------------------------
    # Get Agent
    # -----------------------------------

    def get(self, name):

        return self.agents.get(name)

    # -----------------------------------
    # List Available Agents
    # -----------------------------------

    def available_agents(self):

        return list(self.agents.keys())

    # -----------------------------------
    # Execute Agent
    # -----------------------------------

    def execute(self, name):

        agent = self.get(name)

        if agent is None:

            raise ValueError(
                f"Unknown agent: {name}"
            )

        if hasattr(agent, "execute"):

            return agent.execute()

        if hasattr(agent, "analyze"):

            return agent.analyze()

        raise AttributeError(
            f"{name} has neither execute() nor analyze()."
        )


registry = AgentRegistry()


if __name__ == "__main__":

    print()

    print("Available Agents")

    print("----------------")

    for agent in registry.available_agents():

        print(agent)

    print()

    print("Testing Decision Engine")

    print("-----------------------")

    result = registry.execute("decision")

    print(result)