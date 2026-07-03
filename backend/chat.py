from ai.langgraph.graph import graph
from ai.conversation.session_memory import memory

from ai.shopping.shopping_session import shopping_session
from ai.shopping.orchestrator import ShoppingOrchestrator


def get_cart_summary(phone):
    shopping_session.set_stage(phone, "checkout")
    selected = shopping_session.selected(phone)
    total = 0
    reply = []
    reply.append("🛒 Swiggy Cart Ready")
    reply.append("")
    for item in selected:
        subtotal = (
            item["price"] *
            item["quantity"]
        )
        total += subtotal
        reply.append(
            f"• {item['displayName']}"
        )
        reply.append(
            f"  Qty : {item['quantity']}"
        )
        reply.append(
            f"  ₹{subtotal}"
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
    result = None

    # ==================================================
    # AUTO PROCUREMENT
    # ==================================================

    lower_message = message.lower()

    if (

        lower_message.startswith("order groceries")

        or lower_message.startswith("order everything")

        or lower_message.startswith("buy groceries")

        or lower_message.startswith("today's shopping")

        or lower_message.startswith("procure today's stock")

    ):

        from ai.agents.auto_order_agent import AutoOrderAgent

        agent = AutoOrderAgent()

        result = await agent.execute(

            message=message

        )

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

                if response.get("message") == "AUTO_FINISHED":

                    return get_cart_summary(phone)

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

                if response.get("message") == "AUTO_FINISHED":

                    return get_cart_summary(phone)

                return response["message"]

            return get_cart_summary(phone)

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

        # ----------------------------------------------
        # CHECKOUT RECOVERY
        # ----------------------------------------------

        elif stage == "checkout_recovery":

            session = shopping_session.get(phone)

            selected = session["selected"] if session else []

            recovery_type = session.get("recovery_type") if session else None

            service = ShoppingOrchestrator().service

            orchestrator = ShoppingOrchestrator()

            if recovery_type == "reduce_quantity":

                if message == "1":

                    if selected:

                        selected[-1]["quantity"] = max(1, selected[-1]["quantity"] - 1)

                    await service.build_cart(selected)

                    shopping_session.set_stage(phone, "checkout")

                    return f"Quantity reduced. Updated Cart:\n\n{get_cart_summary(phone)}"

                elif message == "2":

                    if selected:

                        selected.pop()

                        session["current"] = max(0, session["current"] - 1)

                    shopping_session.set_stage(phone, "selecting")

                    response = await orchestrator.resume_session(phone)

                    if response.get("message") == "AUTO_FINISHED":

                        return get_cart_summary(phone)

                    return f"Searching for alternatives:\n\n{response['message']}"

                elif message == "3":

                    if selected:

                        selected.pop()

                    if not selected:

                        shopping_session.end(phone)

                        return "The cart is empty. Shopping session ended."

                    await service.build_cart(selected)

                    shopping_session.set_stage(phone, "checkout")

                    return f"Item removed. Updated Cart:\n\n{get_cart_summary(phone)}"

                else:

                    return "Reply 1 to reduce quantity, 2 to choose another product, or 3 to remove the item."

            elif recovery_type == "choose_alternative":

                if message.lower() == "yes":

                    if selected:

                        selected.pop()

                        session["current"] = max(0, session["current"] - 1)

                    shopping_session.set_stage(phone, "selecting")

                    response = await orchestrator.resume_session(phone)

                    if response.get("message") == "AUTO_FINISHED":

                        return get_cart_summary(phone)

                    return f"Searching for alternatives:\n\n{response['message']}"

                else:

                    shopping_session.end(phone)

                    return "Order cancelled. Shopping session ended."

        if result and (result.get("order_placed") or result.get("success")):

            # pyrefly: ignore [missing-import]
            from ai.memory.memory_trainer import memory_trainer

            memory_trainer.train(

                shopping_session.selected(phone)

            )

            shopping_session.end(phone)

            if result.get("success"):

                return (

                    "✅ Order placed successfully!\n\n"

                    f"Order ID: {result.get('order_id')}\n"

                    f"Status: {result.get('status')}\n"

                    f"Total: ₹{result.get('total')}"

                )

            else:

                return (

                    "✅ Order was submitted successfully, but we encountered an issue parsing the receipt details.\n"

                    f"Detail: {result.get('message')}"

                )

        decision = result.get("decision", {}) if result else {}

        action = decision.get("action")

        # ----------------------------------
        # Reduce Quantity
        # ----------------------------------

        if action == "reduce_quantity":

            session = shopping_session.get(phone)

            if session:

                session["stage"] = "checkout_recovery"

                session["recovery_type"] = "reduce_quantity"

            return (
                "⚠ Some items are available only in a lower quantity.\n\n"
                "Would you like me to:\n\n"
                "1. Reduce the quantity\n"
                "2. Choose another product\n"
                "3. Remove the item"
            )

        # ----------------------------------
        # Alternative Product
        # ----------------------------------

        if action == "choose_alternative":

            session = shopping_session.get(phone)

            if session:

                session["stage"] = "checkout_recovery"

                session["recovery_type"] = "choose_alternative"

            return (
                "⚠ One or more products are unavailable.\n\n"
                "I can search for the closest alternative.\n\n"
                "Reply YES to continue."
            )

        # ----------------------------------
        # Retry
        # ----------------------------------

        if action == "retry":

            return (
                "⚠ Swiggy seems temporarily unavailable.\n\n"
                "Please try again in a few minutes."
            )

        # ----------------------------------
        # Store Closed
        # ----------------------------------

        if action == "change_store":

            return (
                "⚠ The selected store is unavailable.\n\n"
                "I can try another nearby store."
            )

        # ----------------------------------
        # Default
        # ----------------------------------

        return result["message"] if result else "No message."

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

    if "auto_order" in result["results"]:

        shopping_session.start(

            phone,

            result["results"]["auto_order"]["items"]

        )

    elif "procurement" in result["results"]:

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