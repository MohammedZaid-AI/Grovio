import os
import re

from ai.langgraph.graph import graph
from ai.conversation.session_memory import memory

from ai.shopping.shopping_session import shopping_session, _format_candidates
from ai.shopping.orchestrator import ShoppingOrchestrator


# Authorization helpers live in core.authz (a leaf module) so the same
# fail-closed allowlist logic can be shared with the LangGraph nodes without
# introducing a circular import. See security fixes H-1 / H-2.
from core.authz import is_inventory_admin, is_authorized_user


# User-facing message for unauthorized business/financial actions.
UNAUTHORIZED_ACTION_MESSAGE = (
    "❌ *Not Authorized*\n\n"
    "Ordering, purchase-order approvals, and business/financial reports are "
    "restricted to authorized users.\n\n"
    "Contact your administrator if you should have access."
)


# ----------------------------------------------------------------------
# Payment mode (MVP product decision)
# ----------------------------------------------------------------------
# For the MVP every order is placed as Cash on Delivery: the user is never
# asked how they want to pay. The online-payment machinery (get_payment_options,
# the `payment_selection` stage, UPI intent / QR handling and the parser's
# payment fields) is intentionally KEPT INTACT and merely disabled behind this
# flag — flip it back to True to re-enable the payment menu with no other change.
PAYMENT_SELECTION_ENABLED = False

# The Swiggy paymentMethod GROUP value for Cash on Delivery.
COD_PAYMENT_METHOD = "Cash"


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


def _format_payment_options(options, prefix=""):
    """Build the WhatsApp payment-method selection prompt."""
    reply = []
    if prefix:
        reply.append(prefix.rstrip("\n"))
        reply.append("")
    reply.append("💳 *Choose Payment Method*")
    reply.append("")
    for index, opt in enumerate(options, start=1):
        reply.append(f"{index}. {opt['label']}")
    reply.append("")
    reply.append("Reply with the number of your preferred payment method.")
    return "\n".join(reply)


def get_cart_summary(phone):
    shopping_session.set_stage(phone, "checkout")
    shopping_session.advance_lifecycle(phone, "cart_ready")
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


# ----------------------------------------------------------------------
# Checkout revalidation (persistent-session safety net)
# ----------------------------------------------------------------------
def _is_cart_expired(result):
    """Classify a checkout failure as a cart/session expiry (error-message
    classification only — never product logic)."""
    if not isinstance(result, dict):
        return False
    text = f"{result.get('raw_error', '')} {result.get('message', '')}".lower()
    return any(s in text for s in ("cart not found", "session expired",
                                   "cart expired", "no cart", "cart is empty"))


async def _checkout_with_revalidation(service, selected, **checkout_kwargs):
    """The persistent MCP session should still hold the cart, so we checkout
    directly (no rebuild between messages). Only if Swiggy reports the cart
    expired do we rebuild once and retry — production-safe recovery."""
    result = await service.checkout(**checkout_kwargs)
    if _is_cart_expired(result):
        print("[Checkout] Cart reported expired — rebuilding once and retrying.")
        await service.build_cart(selected)
        result = await service.checkout(**checkout_kwargs)
    return result


# ----------------------------------------------------------------------
# Conversation recovery (Problems 4 & 7)
# ----------------------------------------------------------------------
def _human_step(rec):
    stage = rec.get("stage")
    if stage == "selecting":
        item = rec["items"][rec["current"]]["name"] if rec["current"] < len(rec["items"]) else "an item"
        return f"choosing *{item}*"
    if stage == "checkout":
        return "reviewing your cart"
    if stage == "payment_selection":
        return "selecting a payment method"
    if stage == "cod_confirm":
        return "confirming Cash on Delivery"
    if stage == "planning":
        return "about to start shopping"
    return "shopping"


def _resume_prompt(phone):
    rec = shopping_session.get(phone)
    total = len(rec["items"]) if rec.get("items") else 0
    pos = min(rec["current"] + 1, total) if total else 0
    where = _human_step(rec)
    tail = f" (item {pos} of {total})" if total else ""
    return (
        f"👋 Welcome back! You were {where}{tail}.\n\n"
        "Reply *CONTINUE* to pick up where you left off, or *CANCEL* to discard your cart."
    )


def _render_current_step(phone):
    """Re-render the current step's prompt from STORED state only (no new
    Swiggy/LLM calls), so resume is instant and side-effect free."""
    rec = shopping_session.get(phone)
    stage = rec.get("stage")

    if stage == "selecting" and rec.get("options"):
        item = rec["items"][rec["current"]]["name"] if rec["current"] < len(rec["items"]) else ""
        return _format_candidates(item, rec["options"])

    if stage == "payment_selection" and rec.get("payment_options"):
        return _format_payment_options(rec["payment_options"])

    if stage == "cod_confirm":
        return (
            "💵 *Proceeding with Cash on Delivery* (online payment options are "
            "currently unavailable).\n\n"
            "Reply YES to confirm and place the order, or NO to cancel."
        )

    if stage == "planning":
        return "Reply YES to begin shopping."

    # checkout / anything else -> show the cart
    return get_cart_summary(phone)


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

        # H-2: gate real money-spending grocery ordering behind the allowlist.
        if not is_authorized_user(phone):
            return UNAUTHORIZED_ACTION_MESSAGE

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

        # ----------------------------------------------
        # SESSION EXPIRY + CONVERSATION RECOVERY (Problems 4 & 7)
        # ----------------------------------------------
        if shopping_session.is_expired(phone):
            shopping_session.end(phone)
            return (
                "⌛ Your shopping session expired due to inactivity. "
                "Send 'order groceries' to start a new one."
            )

        # Reply to a pending resume prompt.
        if shopping_session.is_awaiting_resume(phone):
            reply = message.strip().lower()
            if reply in ("continue", "resume", "yes", "y"):
                shopping_session.set_awaiting_resume(phone, False)
                shopping_session.touch(phone)
                return _render_current_step(phone)
            if reply in ("cancel", "no", "stop", "discard"):
                shopping_session.end(phone)
                return "🗑️ Your cart was discarded. Send 'order groceries' to start again."
            return _resume_prompt(phone)

        # Long idle gap -> confirm before continuing (never auto-restart).
        if shopping_session.should_offer_resume(phone):
            shopping_session.set_awaiting_resume(phone, True)
            return _resume_prompt(phone)

        shopping_session.touch(phone)

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

            option_count = len(shopping_session.get(phone).get("options", []))
            option_count = min(option_count, 5)

            if not message.isdigit():

                return f"Reply with a number between 1 and {option_count}."

            choice = int(message)

            if choice < 1 or choice > option_count:

                return f"Reply with a number between 1 and {option_count}."

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

            # ONE persistent Swiggy session for this conversation.
            service = shopping_session.get_service(phone)

            # Build the cart ONCE here; the persistent session carries it
            # through payment and checkout (no rebuild between messages).
            await service.build_cart(

                shopping_session.selected(phone)

            )

            shopping_session.advance_lifecycle(phone, "awaiting_confirmation")

            # ---- Online payments (disabled for MVP; see PAYMENT_SELECTION_ENABLED)
            if PAYMENT_SELECTION_ENABLED:

                # Swiggy requires an explicit payment method — never default one.
                # Try to fetch the live options and let the user choose.
                payment_result = await service.get_payment_options()

                if payment_result.get("success") and payment_result.get("options"):

                    options = payment_result["options"]

                    session = shopping_session.get(phone)
                    if session is not None:
                        session["payment_options"] = options

                    shopping_session.set_stage(phone, "payment_selection")

                    return _format_payment_options(options)

                # Fallback: payment options are unavailable for this account
                # (Swiggy-side whitelist / kill switch). Offer Cash on Delivery
                # rather than dead-ending the order, but make it explicit.
                shopping_session.set_stage(phone, "cod_confirm")

                return (
                    "💵 *Proceeding with Cash on Delivery* (online payment options are "
                    "currently unavailable).\n\n"
                    "Reply YES to confirm and place the order, or NO to cancel."
                )

            # ---- MVP: Cash on Delivery only. The user already confirmed the
            # cart with YES, so place the order now — no payment menu, no extra
            # confirmation step.

            # Duplicate-checkout prevention (Problem 6): a second 'yes' must not
            # place a second order.
            if not shopping_session.begin_checkout(phone):
                return "⏳ Your order is already being placed. Please hold on."

            shopping_session.advance_lifecycle(phone, "payment_processing")

            result = await _checkout_with_revalidation(
                service,
                shopping_session.selected(phone),
                payment_method=COD_PAYMENT_METHOD,
            )

        # ----------------------------------------------
        # PAYMENT SELECTION
        # ----------------------------------------------

        elif stage == "payment_selection":

            session = shopping_session.get(phone)

            options = session.get("payment_options", []) if session else []

            if not message.isdigit():

                return _format_payment_options(
                    options,
                    prefix="Please reply with the number of a payment method.\n\n"
                )

            choice = int(message)

            if choice < 1 or choice > len(options):

                return _format_payment_options(
                    options,
                    prefix=f"Please choose a number between 1 and {len(options)}.\n\n"
                )

            selected_method = options[choice - 1]

            # Duplicate-checkout prevention (Problem 6): a second confirmation
            # (e.g. a double 'yes') must not place a second order.
            if not shopping_session.begin_checkout(phone):
                return "⏳ Your order is already being placed. Please hold on."

            shopping_session.advance_lifecycle(phone, "payment_processing")

            service = shopping_session.get_service(phone)

            # Persistent session already holds the cart — checkout directly and
            # only rebuild if Swiggy reports the cart expired.
            result = await _checkout_with_revalidation(
                service,
                shopping_session.selected(phone),
                payment_method=selected_method.get("paymentMethod"),
                intent_app=selected_method.get("intentApp"),
                generate_upi_qr=selected_method.get("generateUPIQR", False),
            )

        # ----------------------------------------------
        # CASH ON DELIVERY CONFIRMATION (fallback)
        # ----------------------------------------------

        elif stage == "cod_confirm":

            if message.lower() != "yes":

                shopping_session.end(phone)

                return "❌ Order cancelled."

            # Duplicate-checkout prevention (Problem 6).
            if not shopping_session.begin_checkout(phone):
                return "⏳ Your order is already being placed. Please hold on."

            shopping_session.advance_lifecycle(phone, "payment_processing")

            service = shopping_session.get_service(phone)

            # Persistent session already holds the cart — checkout directly and
            # only rebuild if Swiggy reports the cart expired.
            result = await _checkout_with_revalidation(
                service,
                shopping_session.selected(phone),
                payment_method="Cash",
            )

        # ----------------------------------------------
        # CHECKOUT RECOVERY
        # ----------------------------------------------

        elif stage == "checkout_recovery":

            session = shopping_session.get(phone)

            selected = session["selected"] if session else []

            recovery_type = session.get("recovery_type") if session else None

            service = shopping_session.get_service(phone)

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

            # Lifecycle: order placed (Problem 6).
            shopping_session.advance_lifecycle(phone, "order_placed")

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

            # Lifecycle: memory learned from the completed order (Problem 6).
            shopping_session.advance_lifecycle(phone, "memory_updated")

            shopping_session.end(phone)

            if result.get("success"):

                # Name the method actually used. In the MVP this is always Cash
                # on Delivery; if online payments are re-enabled the confirmation
                # reflects the real method instead of hardcoding COD.
                payment = result.get("payment")
                if not payment and not PAYMENT_SELECTION_ENABLED:
                    payment = COD_PAYMENT_METHOD

                if str(payment).lower() in ("cash", "cod"):
                    placed = "Your grocery order has been placed successfully using Cash on Delivery."
                elif payment:
                    placed = f"Your grocery order has been placed successfully using {payment}."
                else:
                    placed = "Your grocery order has been placed successfully."

                lines = [
                    "✅ *Order Confirmed!*",
                    "",
                    placed,
                    "",
                    f"Order ID: {result.get('order_id')}",
                ]

                total = result.get("total")
                if total:
                    lines.append(f"Total: ₹{total}")

                lines += [
                    "",
                    "The store will process your order shortly.",
                    "",
                    "Thank you!",
                ]

                return "\n".join(lines)

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

            "phone": phone,

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