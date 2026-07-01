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
    ----------------
    • Auto procurement
    • Shopping workflow
    • LangGraph routing
    • Session memory
    """

    message = message.strip()

    # ==================================================
    # AUTO PROCUREMENT
    # ==================================================

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

    # ==================================================
    # SHOPPING SESSION
    # ==================================================

    if shopping_session.has_session(phone):

        stage = shopping_session.get_stage(phone)

        # ----------------------------------------------
        # PLANNING
        # ----------------------------------------------

        if stage == "planning":

            if message.lower() == "yes":

                shopping_session.set_stage(

                    phone,

                    "selecting"

                )

                orchestrator = ShoppingOrchestrator()

                response = await orchestrator.resume_session(

                    phone

                )

                return response["message"]

            return "Reply YES to begin shopping."

        # ----------------------------------------------
        # SELECTING PRODUCTS
        # ----------------------------------------------

        elif stage == "selecting":

            if not message.isdigit():

                return "Reply with a number between 1 and 5."

            choice = int(message)

            if choice < 1 or choice > 5:

                return "Reply with a number between 1 and 5."

            shopping_session.select(

                phone,

                choice - 1

            )

            orchestrator = ShoppingOrchestrator()

            if not shopping_session.finished(phone):

                response = await orchestrator.resume_session(

                    phone

                )

                return response["message"]

            shopping_session.set_stage(

                phone,

                "checkout"

            )

            selected = shopping_session.selected(phone)

            total = 0

            reply = []

            reply.append("🛒 Swiggy Cart Ready")

            reply.append("")

            for item in selected:

                cost = item["price"] * item["quantity"]

                total += cost

                reply.append(f"• {item['displayName']}")
                reply.append(f"Qty : {item['quantity']}")
                reply.append(f"₹{cost}")
                reply.append("")

            reply.append(f"Estimated Total : ₹{total}")
            reply.append("")
            reply.append("Reply YES to place the order.")

            return "\n".join(reply)

        # ----------------------------------------------
        # CHECKOUT
        # ----------------------------------------------

        elif stage == "checkout":

            if message.lower() != "yes":

                return "Reply YES to place the order."

            service = ShoppingOrchestrator().service

            await service.build_cart(

                shopping_session.selected(phone)

            )

            result = await service.checkout()

            shopping_session.end(phone)

            return result["message"]

    # ==================================================
    # LANGGRAPH
    # ==================================================

    result = graph.invoke(

        {

            "message": message,

            "selected_agents": [],

            "results": {},

            "response": ""

        }

    )

    # ==================================================
    # SESSION MEMORY
    # ==================================================

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