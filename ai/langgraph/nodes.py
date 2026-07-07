from ai.langgraph.registry import registry
from ai.langgraph.supervisor import Supervisor
from core.formatters import format_quantity
import asyncio


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

        instance = registry.get(agent)

        # Purchase Editor
        if agent == "purchase_editor":

            results[agent] = instance.execute(

                state["message"]

            )

        # Purchase History
        elif agent == "purchase_history":

            results[agent] = instance.execute(

                state["message"]

            )

        # Async Agents
        elif agent == "auto_order":

            import concurrent.futures

            def run_in_new_loop(coro):
                loop = asyncio.new_event_loop()
                try:
                    asyncio.set_event_loop(loop)
                    return loop.run_until_complete(coro)
                finally:
                    loop.close()

            with concurrent.futures.ThreadPoolExecutor() as executor:
                future = executor.submit(run_in_new_loop, instance.execute(state["message"]))
                results[agent] = future.result()

        # Dashboard
        elif agent == "dashboard":

            results[agent] = instance.execute()

        # Default
        else:

            results[agent] = registry.execute(

                agent,

                state["message"]

            )

    state["results"] = results

    return state


def response_node(state):

    if "inventory_manager" in state["results"]:

        state["response"] = state["results"]["inventory_manager"]["message"]

    elif "inventory_query" in state["results"]:

        state["response"] = state["results"]["inventory_query"]["message"]

    elif "procurement" in state["results"]:

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
        import inspect
        import hashlib
        try:
            func = response_node
            src = inspect.getsource(func)
            src_hash = hashlib.sha256(src.encode('utf-8')).hexdigest()[:10]
            print(f"\n[DEBUG_FORMATTER] File: {inspect.getfile(func)}, StartLine: {inspect.getsourcelines(func)[1]}, Hash: {src_hash}\n")
        except Exception as debug_err:
            print(f"\n[DEBUG_FORMATTER_ERROR] {debug_err}\n")

        data = state["results"]["decision"]
        
        reply = []
        reply.append("🧠 *Grovio Restaurant Intelligence Report*")
        reply.append("")
        
        # 1. Restaurant Health
        health = data.get("restaurant_health", {})
        reply.append(f"🏥 *Restaurant Health*: {health.get('status', 'Unknown')} (Score: {health.get('score', 0)}/100)")
        if health.get("reasons"):
            for reason in health["reasons"]:
                reply.append(f"  • {reason}")
        reply.append("")
        
        # 2. Inventory Health
        inv = data.get("inventory", {})
        reply.append(f"📦 *Inventory Health*: {inv.get('status', 'Unknown')} (Score: {inv.get('health_score', 0)}/100)")
        if inv.get("low_stock"):
            reply.append("  ⚠️ *Low Stock Items*:")
            for item in inv["low_stock"]:
                if isinstance(item, dict):
                    p_name = item.get('product', 'Unknown')
                    p_stock = format_quantity(item.get('stock', 0))
                    p_unit = item.get('unit', '')
                    p_min = format_quantity(item.get('minimum', 0))
                    reply.append(f"    • {p_name}: {p_stock} {p_unit} (min: {p_min})")
                elif isinstance(item, (list, tuple)) and len(item) >= 5:
                    p_stock = format_quantity(item[2])
                    p_min = format_quantity(item[3])
                    reply.append(f"    • {item[1]}: {p_stock} {item[4]} (min: {p_min})")
                else:
                    reply.append(f"    • {item}")
        reply.append("")
        
        # 3. Procurement Forecast
        proc = data.get("procurement", {})
        reply.append(f"📈 *Procurement Forecast*: {proc.get('action', 'WAIT_FOR_MORE_DATA')} (Confidence: {proc.get('confidence', 0)}%)")
        forecast = proc.get("forecast", {})
        recommended_orders = forecast.get("recommended_orders", [])
        if recommended_orders:
            reply.append("  📋 *Recommended Purchases*:")
            for rec in recommended_orders:
                p_name = rec.get('product', 'Unknown')
                p_qty = rec.get('recommended_quantity', 0)
                p_prob = rec.get('purchase_probability', 0.0)
                p_reason = rec.get('reason', '')
                unit_label = "unit" if p_qty == 1 else "units"
                reply.append(f"    • {p_name}: {p_qty} {unit_label} ({int(p_prob)}% purchase probability) — {p_reason}")
        reply.append("")
        
        # 4. Risks & Opportunities
        risks = data.get("risks", [])
        if risks:
            reply.append("⚠️ *Business Risks*:")
            for risk in risks:
                reply.append(f"  • {risk}")
            reply.append("")
            
        opps = data.get("opportunities", [])
        if opps:
            reply.append("💡 *Opportunities*:")
            for opp in opps:
                reply.append(f"  • {opp}")
            reply.append("")
            
        # 5. Daily Brief Summary
        brief = data.get("daily_brief", {})
        if brief:
            reply.append("📜 *Daily Brief Summary*:")
            reply.append(f"  • Date: {brief.get('date', 'N/A')}")
            reply.append(f"  • Spend today: ₹{brief.get('restaurant_spend', 0.0)}")
            reply.append(f"  • Pending orders: {brief.get('pending_orders', 0)}")
            reply.append(f"  • Completed orders: {brief.get('completed_orders', 0)}")
            if brief.get("insights"):
                reply.append("  *Insights*:")
                for insight in brief["insights"]:
                    reply.append(f"    ✓ {insight}")
                    
        state["response"] = "\n".join(reply).strip()
    
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
    elif "auto_order" in state["results"]:

        state["response"] = state["results"]["auto_order"]["message"]

    elif "receive_order" in state["results"]:

        state["response"] = state["results"]["receive_order"]

    elif "restaurant_memory" in state["results"]:

        state["response"] = state["results"]["restaurant_memory"]

    else:

        state["response"] = "No response."

    return state