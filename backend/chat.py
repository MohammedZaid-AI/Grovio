from ai.langgraph.graph import graph
from ai.conversation.session_memory import memory


def process_message(
    phone,
    message
):
    """
    Sends every message to LangGraph.

    LangGraph Supervisor decides which
    agent(s) should handle the request.
    """

    result = graph.invoke(

        {

            "message": message,

            "selected_agents": [],

            "results": {},

            "response": ""

        }

    )

    # ------------------------------------
    # Update Session Memory
    # ------------------------------------

    if "procurement" in result["results"]:

        purchase_order = result["results"]["procurement"]["purchase_order"]

        memory.update(

            phone,

            last_agent="procurement",

            last_purchase_order=purchase_order["purchase_order_id"],

            awaiting_approval=True

        )

    elif "purchase_approval" in result["results"]:

        memory.update(

            phone,

            last_agent="purchase_approval",

            awaiting_approval=False

        )

    elif "purchase_rejection" in result["results"]:

        memory.update(

            phone,

            last_agent="purchase_rejection",

            awaiting_approval=False

        )

    elif result["selected_agents"]:

        memory.update(

            phone,

            last_agent=result["selected_agents"][-1]

        )

    print()

    print("=" * 70)

    print("LangGraph Result")

    print(result)

    print("=" * 70)

    print()

    return result["response"]