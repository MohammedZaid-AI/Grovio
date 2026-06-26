from langgraph.graph import StateGraph, END

from ai.langgraph.state import RestaurantState

from ai.langgraph.nodes import (

    supervisor_node,

    execute_agents,

    response_node

)

builder = StateGraph(

    RestaurantState

)

builder.add_node(

    "supervisor",

    supervisor_node

)

builder.add_node(

    "execute",

    execute_agents

)

builder.add_node(

    "response",

    response_node

)

builder.set_entry_point(

    "supervisor"

)

builder.add_edge(

    "supervisor",

    "execute"

)

builder.add_edge(

    "execute",

    "response"

)

builder.add_edge(

    "response",

    END

)

graph = builder.compile()


if __name__ == "__main__":

    result = graph.invoke(

        {

            "message":

                "Give me today's report",

            "selected_agents": [],

            "results": {},

            "response": ""

        }

    )

    print()

    print(result["response"])