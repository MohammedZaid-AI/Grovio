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

        results[agent] = registry.execute(agent)

    state["results"] = results

    return state


def response_node(state):

    if "coo" in state["results"]:

        state["response"] = state["results"]["coo"]["analysis"]

    elif "decision" in state["results"]:

        state["response"] = str(

            state["results"]["decision"]

        )

    else:

        state["response"] = "No response."

    return state