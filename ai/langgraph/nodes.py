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

        elif agent == "purchase_history":

            results[agent] = registry.get(

                agent

            ).execute(

                state["message"]

            )
        
        elif agent == "dashboard":

            results[agent] = registry.execute(agent)

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
    
    elif "purchase_history" in state["results"]:

        state["response"] = state["results"]["purchase_history"]["message"]
    
    elif "dashboard" in state["results"]:

        data = state["results"]["dashboard"]

        reply = []

        reply.append("📊 Restaurant Dashboard")

        reply.append("")

        reply.append(f"✅ Completed Orders : {data['completed_orders']}")

        reply.append(f"⏳ Pending Orders : {data['pending_orders']}")

        reply.append("")

        reply.append(f"📦 Inventory : {data['inventory_status']} ({data['inventory_health']}%)")

        reply.append(f"💰 Procurement Spend : ₹{data['total_spend']}")

        reply.append(f"🧾 Invoices : {data['invoice_count']}")

        reply.append(f"📈 Forecast Confidence : {data['forecast_confidence']}%")

        reply.append(f"🧠 Restaurant Health : {data['restaurant_health']}")

        if data["risks"]:

            reply.append("")

            reply.append("⚠ Risks")

            for risk in data["risks"]:

                reply.append(f"• {risk}")

        state["response"] = "\n".join(reply)

    else:

        state["response"] = "No response."

    return state