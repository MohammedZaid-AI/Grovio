from ai.langgraph.registry import registry
from ai.langgraph.supervisor import Supervisor


supervisor = Supervisor()


def supervisor_node(state):

    agents = supervisor.route(

        state["message"]

    )

    state["selected_agents"] = agents

    return state


def execute_agents(state):

    results = {}

    for agent in state["selected_agents"]:

        if agent == "purchase_editor":

            results[agent] = registry.get(agent).execute(

                state["message"]

            )

        else:

            results[agent] = registry.execute(agent)

    state["results"] = results

    return state


def response_node(state):

    if "procurement" in state["results"]:

        state["response"] = state["results"]["procurement"]["message"]

    elif "purchase_editor" in state["results"]:

        state["response"] = state["results"]["purchase_editor"]["message"]

    elif "purchase_approval" in state["results"]:

        state["response"] = state["results"]["purchase_approval"]["message"]

    elif "purchase_rejection" in state["results"]:

        state["response"] = state["results"]["purchase_rejection"]["message"]

    elif "coo" in state["results"]:

        state["response"] = state["results"]["coo"]["analysis"]

    elif "decision" in state["results"]:

        state["response"] = str(

            state["results"]["decision"]

        )

    else:

        state["response"] = "No response."

    return state