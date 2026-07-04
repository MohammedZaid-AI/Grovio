import json
import re
from datetime import datetime
from core.llm import llm
from db import (
    set_manual_product_preference,
    get_product_memory,
    get_incoming_non_received_inventory_item,
    get_base_product,
    get_supplier_reliability,
    get_supplier_leaderboard
)

SYSTEM_PROMPT = """You are the Grovio Restaurant Memory Agent.
Your job is to analyze the user's message and categorize it into one of the following intents:

1. If the user wants to set a preference (manual override for a brand or supplier of a product):
   Return JSON:
   {
       "action": "set_preference",
       "product": "<product_name>",
       "preferred_brand": "<brand_name_or_null>",
       "preferred_supplier": "<supplier_name_or_null>"
   }
   NOTE: Extract brand names (e.g. "Nandini", "Amul") or supplier names (e.g. "ABC Dairy"). Set to null if not specified.

2. If the user wants to query memory/history for a product (e.g. last purchase date, brand preference, reorder frequency, usual order day, or seasonal spikes):
   Return JSON:
   {
       "action": "query_memory",
       "product": "<product_name>"
   }

3. If the user wants to query reliability/metrics/scores for a specific supplier (e.g. "how reliable is ABC Dairy?", "show delivery stats for ABC Dairy"):
   Return JSON:
   {
       "action": "query_supplier_reliability",
       "supplier": "<supplier_name>"
   }

4. If the user wants to query the leaderboard/ranking of all suppliers (e.g. "show supplier leaderboard", "which supplier is most reliable?"):
   Return JSON:
   {
       "action": "query_supplier_leaderboard"
   }

5. If the user asks for the recipe of a dish (e.g. "what is the recipe for chicken steak?", "show recipe for cheese toast"):
   Return JSON:
   {
       "action": "recipe_query",
       "dish_name": "<dish_name>"
   }

6. If the user asks for ingredient consumption history, usage metrics, or daily consumption averages based on sales (e.g. "how much chicken did we use this week?", "show consumption summary", "how much milk was used based on sales?"):
   Return JSON:
   {
       "action": "query_consumption",
       "product": "<product_name_or_null>"
   }

7. Otherwise:
   Return JSON:
   {
       "action": "unknown"
   }

Choose exactly one intent. Return ONLY JSON. Never explain.
"""

class RestaurantMemoryAgent:
    """
    Agent responsible for querying and managing product preferences,
    brands, suppliers, reorder intervals, purchase history, and supplier reliability.
    """
    
    def execute(self, message: str) -> str:
        if not message:
            return "No memory details provided."
            
        msg_strip = message.strip()
        msg_lower = msg_strip.lower()
        if msg_lower.startswith("recipe:"):
            parts = msg_strip.split("=", 1)
            if len(parts) == 2:
                header = parts[0]
                try:
                    dish_name = header.split(":", 1)[1].strip()
                except IndexError:
                    return "❌ Invalid recipe format. Use: 'Recipe: Chicken Steak = 200g Chicken, 50g Mixed Veg'"
                ingredients_str = parts[1].strip()
                
                ingredients = []
                segments = ingredients_str.split(",")
                for seg in segments:
                    seg = seg.strip()
                    if not seg:
                        continue
                    match = re.match(r"^(\d+(?:\.\d+)?)\s*([a-zA-Z]+)?\s+(.+)$", seg)
                    if match:
                        qty = float(match.group(1))
                        unit = match.group(2) or "unit"
                        ing_name = match.group(3).strip()
                        ingredients.append({
                            'ingredient_name': ing_name,
                            'quantity_per_unit': qty,
                            'unit': unit
                        })
                
                if dish_name and ingredients:
                    from db import save_recipe
                    save_recipe(dish_name, ingredients)
                    lines = [f"✅ Recipe saved for *{dish_name}*:"]
                    for ing in ingredients:
                        lines.append(f"• {ing['quantity_per_unit']} {ing['unit']} of {ing['ingredient_name']}")
                    return "\n".join(lines)
                else:
                    return "❌ Invalid recipe format. Use: 'Recipe: Chicken Steak = 200g Chicken, 50g Mixed Veg'"
            else:
                return "❌ Invalid recipe format. Use: 'Recipe: Chicken Steak = 200g Chicken, 50g Mixed Veg'"
            
        # Parse intent using LLM
        response = llm.chat(
            system=SYSTEM_PROMPT,
            user=message,
            temperature=0
        )
        
        try:
            # Clean JSON response
            json_match = re.search(r"({.*})|(\[.*\])", response, re.DOTALL)
            clean_response = json_match.group(0) if json_match else response
            data = json.loads(clean_response)
            
            action = data.get("action", "unknown")
            
            if action == "set_preference":
                product = data.get("product", "").strip()
                if not product:
                    return "Could not determine the product name for preference settings."
                preferred_brand = data.get("preferred_brand")
                preferred_supplier = data.get("preferred_supplier")
                
                if preferred_brand in ("null", "None", ""):
                    preferred_brand = None
                if preferred_supplier in ("null", "None", ""):
                    preferred_supplier = None
                    
                set_manual_product_preference(
                    product_name=product,
                    preferred_brand=preferred_brand,
                    preferred_supplier=preferred_supplier
                )
                
                confirmations = []
                if preferred_brand:
                    confirmations.append(f"brand preferred as *{preferred_brand}*")
                if preferred_supplier:
                    confirmations.append(f"supplier preferred as *{preferred_supplier}*")
                    
                msg = ", ".join(confirmations)
                return f"✅ Preference saved for *{product.capitalize()}*: We now keep {msg} (source: MANUAL)."
                
            elif action == "query_memory":
                product = data.get("product", "").strip()
                if not product:
                    return "Could not determine which product you are querying memory for."
                base_prod = get_base_product(product)
                mem = get_product_memory(base_prod)
                pending_date = get_incoming_non_received_inventory_item(base_prod)
                
                if not mem or mem['confidence_level'] == 'NONE':
                    return f"I don't have enough purchasing history or preferences stored for '{product}' yet."
                    
                brand_str = f"{mem['preferred_brand']} (source: {mem['preferred_brand_source']})" if mem['preferred_brand'] else "None detected"
                supplier_str = f"{mem['preferred_supplier']} (source: {mem['preferred_supplier_source']})" if mem['preferred_supplier'] else "None detected"
                interval_str = f"{mem['avg_reorder_interval']:.1f} days" if mem['avg_reorder_interval'] else "Not calculated"
                last_date_str = mem['last_purchase_date'] if mem['last_purchase_date'] else "No purchases recorded"
                pending_str = f"Yes, expected on {pending_date}" if pending_date else "None pending"
                
                usual_day = mem.get('usual_day_of_week')
                usual_day_conf = mem.get('usual_day_of_week_confidence', 'NONE')
                usual_day_str = f"{usual_day} (Confidence: {usual_day_conf})" if usual_day else f"Not calculated (Confidence: {usual_day_conf})"
                
                spikes_val = mem.get('seasonal_spikes')
                if spikes_val:
                    try:
                        spikes_list = json.loads(spikes_val)
                        spikes_str = ", ".join(spikes_list)
                    except:
                        spikes_str = "None detected"
                else:
                    spikes_str = "None detected"
                
                reply = [
                    f"🧠 *Grovio Restaurant Memory: {product.capitalize()}*",
                    "",
                    f"• *Preferred Brand*: {brand_str}",
                    f"• *Preferred Supplier*: {supplier_str}",
                    f"• *Average Reorder Interval*: {interval_str} (Confidence: {mem['confidence_level']})",
                    f"• *Usual Purchase Day*: {usual_day_str}",
                    f"• *Seasonal Spikes*: {spikes_str}",
                    f"• *Last Purchase Date*: {last_date_str}",
                    f"• *Pending Deliveries*: {pending_str}"
                ]
                return "\n".join(reply)
                
            elif action == "query_supplier_reliability":
                supplier_val = data.get("supplier")
                supplier = supplier_val.strip() if supplier_val else ""
                if not supplier:
                    return "Could not determine which supplier you are asking about."
                rel = get_supplier_reliability(supplier)
                
                if not rel or rel['confidence_level'] == 'NONE':
                    return f"I don't have any delivery history tracked for '{supplier}' yet."
                    
                total_del = rel['total_deliveries']
                if rel['confidence_level'] == 'LOW':
                    return (
                        f"🚚 *Supplier Reliability: {supplier}*\n\n"
                        f"• *Confidence Level*: LOW ({total_del} deliveries tracked)\n"
                        f"• *Status*: Not enough delivery history yet (requires at least 3 completed deliveries for scoring)."
                    )
                
                acc_rate = rel['accuracy_rate']
                avg_delay = rel['avg_delay_days']
                qty_acc = rel['quantity_accuracy_rate']
                
                return (
                    f"🚚 *Supplier Reliability: {supplier}*\n\n"
                    f"• *Confidence Level*: HIGH ({total_del} deliveries tracked)\n"
                    f"• *On-Time Rate*: {acc_rate:.1f}% ({rel['on_time_deliveries']}/{total_del} on time)\n"
                    f"• *Average Delay*: {avg_delay:.1f} days\n"
                    f"• *Quantity Accuracy*: {qty_acc:.1f}% of items fully received"
                )
                
            elif action == "query_supplier_leaderboard":
                leaderboard = get_supplier_leaderboard()
                if not leaderboard:
                    return "I don't have enough supplier delivery history to build a leaderboard yet."
                    
                rated_lines = []
                unrated_lines = []
                
                for idx, entry in enumerate(leaderboard):
                    s_name = entry['supplier_name']
                    if entry['confidence_level'] == 'HIGH':
                        # Rated
                        acc = entry['accuracy_rate']
                        qty = entry['quantity_accuracy_rate']
                        rated_lines.append(f"{len(rated_lines)+1}. {s_name} (Reliability: {acc:.1f}%, Qty Accuracy: {qty:.1f}%)")
                    else:
                        # Unrated / Low confidence
                        cursor_conn = get_supplier_reliability(s_name)
                        total_del = cursor_conn['total_deliveries'] if cursor_conn else 0
                        unrated_lines.append(f"• {s_name} ({total_del} delivery tracked)" if total_del == 1 else f"• {s_name} ({total_del} deliveries tracked)")
                        
                reply = [
                    "🏆 *Grovio Supplier Leaderboard*",
                    ""
                ]
                if rated_lines:
                    reply.extend(rated_lines)
                else:
                    reply.append("No rated suppliers yet.")
                    
                if unrated_lines:
                    reply.append("")
                    reply.append("*Pending History (Not Rated Yet)*:")
                    reply.extend(unrated_lines)
                    
                reply.append("")
                reply.append("(Suppliers require at least 3 completed deliveries for scoring).")
                return "\n".join(reply)
                
            elif action == "recipe_query":
                dish_val = data.get("dish_name")
                dish_name = dish_val.strip() if dish_val else ""
                if not dish_name:
                    return "Could not determine which dish you are asking about."
                from db import get_recipe
                recipe = get_recipe(dish_name)
                if not recipe:
                    return f"I don't have a recipe defined for *{dish_name}* yet. You can define one by replying:\n'Recipe: {dish_name} = 200g Chicken, 50g Mixed Veg'"
                
                reply = [f"📋 *Recipe: {dish_name}*", ""]
                for ing in recipe:
                    reply.append(f"• {ing['ingredient_name'].capitalize()}: {ing['quantity_per_unit']} {ing['unit']}")
                reply.append("")
                reply.append(f'(Reply "Recipe: {dish_name} = ..." to update).')
                return "\n".join(reply)
                
            elif action == "query_consumption":
                product_val = data.get("product")
                product = product_val.strip() if product_val else ""
                from db import get_product_consumption_stats, get_weekly_consumption_summary, get_product_inventory
                
                if product and product.lower() != "null" and product.lower() != "none" and product != "":
                    base_prod = get_base_product(product)
                    stats = get_product_consumption_stats(base_prod)
                    total = stats.get('total_consumed', 0.0)
                    unit = stats.get('unit') or 'units'
                    adcr = stats.get('adcr')
                    conf = stats.get('adcr_confidence', 'NONE')
                    
                    inv = get_product_inventory(base_prod)
                    current_stock = inv[2] if inv and inv[2] is not None else 0.0
                    inv_unit = inv[4] if inv and inv[4] is not None else unit
                    
                    reply = [
                        f"📊 *Sales-Based Consumption: {base_prod.capitalize()}*",
                        "",
                        f"• *Total Consumed*: {total:.1f} {unit}",
                    ]
                    if conf == 'HIGH' and adcr:
                        days_left = current_stock / adcr if adcr > 0 else 999.0
                        reply.append(f"• *Average Daily Usage*: {adcr:.1f} {unit}/day")
                        reply.append(f"• *Current Stock Status*: {current_stock:.1f} {inv_unit} remaining (~{days_left:.1f} days left)")
                        reply.append("• *Confidence*: HIGH")
                    else:
                        reply.append("• *Average Daily Usage*: not enough sales data yet")
                        reply.append(f"• *Current Stock Status*: {current_stock:.1f} {inv_unit} remaining")
                        reply.append("• *Confidence*: LOW")
                    return "\n".join(reply)
                else:
                    # General summary
                    summary = get_weekly_consumption_summary()
                    if not summary:
                        return "No ingredient consumption recorded over the last 7 days."
                    reply = [
                        "📈 *Weekly Ingredient Consumption Summary*",
                        ""
                    ]
                    for item in summary:
                        prod = item['product_name']
                        qty = item['consumed_quantity']
                        unit = item['unit']
                        
                        stats = get_product_consumption_stats(prod)
                        adcr = stats.get('adcr')
                        conf = stats.get('adcr_confidence', 'NONE')
                        
                        inv = get_product_inventory(prod)
                        current_stock = inv[2] if inv and inv[2] is not None else 0.0
                        inv_unit = inv[4] if inv and inv[4] is not None else unit
                        
                        stock_status = ""
                        if conf == 'HIGH' and adcr and adcr > 0:
                            days_left = current_stock / adcr
                            stock_status = f" (~{days_left:.1f} days stock left)"
                            
                        reply.append(f"• *{prod.capitalize()}*: {qty:.1f} {unit} used{stock_status}")
                    
                    reply.append("")
                    reply.append("(Calculated from sales receipts over the last 7 days).")
                    return "\n".join(reply)
                    
            else:
                return (
                    "I couldn't understand your memory request. You can ask me questions like "
                    "'when did we last order milk?', 'how reliable is ABC Dairy?', or 'show supplier leaderboard'."
                )
                
        except Exception as e:
            print(f"Error in RestaurantMemoryAgent execution: {e}")
            return "Sorry, I encountered an error while updating or retrieving restaurant memory."
