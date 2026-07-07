import os
import re

from ai.langgraph.graph import graph
from ai.conversation.session_memory import memory

from ai.shopping.shopping_session import shopping_session
from ai.shopping.orchestrator import ShoppingOrchestrator


def is_inventory_admin(from_phone: str) -> bool:
    """
    Check if phone is authorized for inventory commands.

    SECURITY: Fails CLOSED (blocks all) if not configured.
    A missing INVENTORY_ADMIN_PHONES should never grant access.
    """
    admin_phones_config = os.getenv("INVENTORY_ADMIN_PHONES", "").strip()

    # NOT configured → block all (fail closed)
    if not admin_phones_config:
        return False

    # Parse comma-separated list
    admin_list = [p.strip() for p in admin_phones_config.split(",") if p.strip()]

    # Normalize incoming phone (strip "whatsapp:" prefix)
    normalized_phone = from_phone.replace("whatsapp:", "").strip()

    return normalized_phone in admin_list


def _is_inventory_command(message: str) -> bool:
    """Detect if message is an inventory SET/ADD/REMOVE command."""
    msg_lower = message.lower().strip()

    # SET pattern: "set [product] stock to..."
    if re.search(r"^set\s+.+\s+stock\s+to\s+", msg_lower):
        return True

    # ADD pattern: "add [qty] [unit] [product]"
    if re.search(r"^add\s+[\d.]+\s+[a-z]+\s+", msg_lower):
        return True

    # REMOVE pattern: "remove [qty] [unit] [product]"
    if re.search(r"^(?:remove|subtract)\s+[\d.]+\s+[a-z]+\s+", msg_lower):
        return True

    return False


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
    # PENDING DOCUMENT CONFIRMATION (Safety Net)
    # ==================================================
    from db import get_latest_pending_document, update_pending_document_status
    pending_doc = get_latest_pending_document(phone)
    if pending_doc:
        msg_clean = message.strip().lower()
        if msg_clean in ["yes", "no", "y", "n"]:
            doc_id = pending_doc["id"]
            doc_type = pending_doc["doc_type"]
            payload = pending_doc["payload"]
            
            if msg_clean in ["yes", "y"]:
                update_pending_document_status(doc_id, "CONFIRMED")
                
                if doc_type == "SUPPLIER_INVOICE":
                    from ai.invoice.processor import InvoiceProcessor
                    proc = InvoiceProcessor()
                    res = proc.process(payload)
                    if res.get("success"):
                        return (
                            "✅ *Supplier Invoice Confirmed & Logged*\n\n"
                            "Inventory has been updated.\n"
                            "Price history has been updated."
                        )
                    else:
                        return f"❌ Failed to process supplier invoice: {res.get('message', 'Unknown error')}"
                
                elif doc_type == "SALES_BILL":
                    from db import save_sales_bill, confirm_sales_bill
                    from datetime import datetime
                    bill_number = payload.get("invoice_number") or f"SB-{doc_id}"
                    bill_date = payload.get("date") or datetime.now().strftime("%Y-%m-%d")
                    total_amount = payload.get("total_amount") or 0.0
                    
                    items = []
                    for item in payload.get("items", []):
                        qty = item.get("quantity")
                        if qty is not None and qty > 0:
                            items.append({
                                "dish_name": item.get("product"),
                                "quantity": int(qty),
                                "unit_price": item.get("unit_price"),
                                "total_price": item.get("total")
                            })
                    
                    bill_db_id = save_sales_bill(bill_number, bill_date, total_amount, items, status='PENDING_CONFIRMATION')
                    confirm_sales_bill(bill_db_id)
                    
                    return (
                        f"✅ *Sales Bill Confirmed & Logged*\n\n"
                        f"Bill Number: {bill_number}\n"
                        f"Dishes sold: {len(items)}\n"
                        f"Ingredient consumption has been calculated."
                    )
            else:
                update_pending_document_status(doc_id, "CANCELLED")
                return f"❌ Document confirmation cancelled. The draft (ID: {doc_id}) was discarded."

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

            try:
                from db import save_swiggy_order
                import time

                order_id = result.get("order_id")
                if not order_id:
                    order_id = f"SWG-{int(time.time())}"

                items_selected = shopping_session.selected(phone)
                items_list = [[item.get("displayName", "Unknown"), item.get("quantity", 1)] for item in items_selected]

                total_amount = result.get("total")
                if not total_amount:
                    total_amount = sum(item.get("price", 0) * item.get("quantity", 0) for item in items_selected)

                status_str = result.get("status", "CONFIRMED")

                save_swiggy_order(
                    order_id=order_id,
                    items=items_list,
                    total=total_amount,
                    status=status_str,
                    phone=phone
                )
            except Exception as db_err:
                print(f"❌ Error persisting Swiggy order: {db_err}")

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
    # INVENTORY MANAGEMENT (WITH ACCESS CONTROL)
    # ==================================================

    if _is_inventory_command(message):
        if not is_inventory_admin(phone):
            return "❌ *Not Authorized*\n\nInventory commands (Set/Add/Remove stock) are restricted to admin users.\n\nContact your administrator if you should have access."

        from ai.agents.inventory_manager_agent import InventoryManagerAgent
        agent = InventoryManagerAgent()
        result = agent.execute(message, user_phone=phone)
        return result.get("message", "Inventory command processed.")

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