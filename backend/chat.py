from ai.langgraph.graph import graph
from ai.conversation.session_memory import memory

from ai.shopping.shopping_session import shopping_session
from ai.shopping.orchestrator import ShoppingOrchestrator


async def process_message(
    phone,
    message
):
    """
    Main backend chat router.

    Responsibilities

    • Auto procurement
    • Shopping continuation
    • LangGraph routing
    • Session memory
    """

    message = message.strip()

    # --------------------------------------------------
    # Auto Procurement
    # --------------------------------------------------

    if message.lower() in {

        "order groceries",

        "order everything",

        "buy groceries",

        "today's shopping",

        "procure today's stock"

    }:

        from ai.agents.auto_order_agent import AutoOrderAgent

        agent = AutoOrderAgent()

        result = await agent.execute()

        shopping_session.start(

            phone,

            result["items"]

        )

        return result["message"]

    # --------------------------------------------------
    # Continue Shopping Session
    # --------------------------------------------------

    if shopping_session.has_session(phone):

        # Start selecting products

        if message.lower() == "yes":

            orchestrator = ShoppingOrchestrator()

            response = await orchestrator.start(

                phone=phone,

                source="session"

            )

            return response["message"]
        
    # ------------------------------------------
    # Product Selection (1-5)
    # ------------------------------------------

        if message.isdigit():

            choice = int(message)

            if 1 <= choice <= 5:

                shopping_session.select(

                    phone,

                    choice - 1

                )

                orchestrator = ShoppingOrchestrator()

                # More products remaining
                if not shopping_session.finished(phone):

                    response = await orchestrator.resume_session(

                        phone

                    )

                    return response["message"]

                # Shopping finished
                session = shopping_session.get(phone)

                selected = session["selected"]

                total = 0

                reply = []

                reply.append("🛒 Swiggy Cart Ready")

                reply.append("")

                for item in selected:

                    cost = item["price"] * item["quantity"]

                    total += cost

                    reply.append(

                        f"• {item['displayName']}"

                    )

                    reply.append(

                        f"Qty : {item['quantity']}"

                    )

                    reply.append(

                        f"₹{cost}"

                    )

                    reply.append("")

                reply.append(

                    f"Estimated Total : ₹{total}"

                )

                reply.append("")

                reply.append(

                    "Reply YES to place the order."

                )

                return "\n".join(reply)

            return "Please reply with a number between 1 and 5."

    # --------------------------------------------------
    # LangGraph
    # --------------------------------------------------

    result = graph.invoke(

        {

            "message": message,

            "selected_agents": [],

            "results": {},

            "response": ""

        }

    )

    # --------------------------------------------------
    # Session Memory
    # --------------------------------------------------

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