from ai.agents.ai_coo import AICOO
from ai.intelligence.decision_engine import DecisionEngine
from ai.agents.procurement_agent import ProcurementAgent
from ai.agents.purchase_approval_agent import PurchaseApprovalAgent
from ai.agents.purchase_rejection_agent import PurchaseRejectionAgent
from ai.agents.purchase_history_agent import PurchaseHistoryAgent
from ai.agents.purchase_order_editor_agent import PurchaseOrderEditorAgent
from ai.agents.dashboard_agent import DashboardAgent
from ai.agents.auto_order_agent import AutoOrderAgent
from ai.agents.receive_order_agent import ReceiveOrderAgent

class AgentRegistry:

    """
    Central registry of all AI agents.
    """

    def __init__(self):

        self.agents = {

            "coo": AICOO(),

            "decision": DecisionEngine(),

            "procurement": ProcurementAgent(),

            "purchase_approval": PurchaseApprovalAgent(),

            "purchase_rejection": PurchaseRejectionAgent(),

            "purchase_editor": PurchaseOrderEditorAgent(),

            "purchase_history": PurchaseHistoryAgent(),

            "dashboard": DashboardAgent(),

            "auto_order": AutoOrderAgent(),

            "receive_order": ReceiveOrderAgent(),

        }

    # -----------------------------------
    # Get Agent
    # -----------------------------------

    def get(self, name):

        return self.agents.get(name)

    # -----------------------------------
    # Available Agents
    # -----------------------------------

    def available_agents(self):

        return list(self.agents.keys())

    # -----------------------------------
    # Execute Agent
    # -----------------------------------

    def execute(self, name, message=None):

        agent = self.get(name)

        if agent is None:

            raise ValueError(

                f"Unknown agent: {name}"

            )

        if hasattr(agent, "execute"):
            import inspect
            sig = inspect.signature(agent.execute)
            if 'message' in sig.parameters:
                return agent.execute(message)
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

    print("Testing Procurement")

    print("-------------------")

    print(

        registry.execute(

            "procurement"

        )

    )