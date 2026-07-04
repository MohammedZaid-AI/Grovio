import json
import re
from datetime import datetime
from core.llm import llm
from db import (
    set_manual_product_preference,
    get_product_memory,
    get_incoming_non_received_inventory_item,
    get_base_product
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

2. If the user wants to query memory/history for a product (e.g. last purchase date, brand preference, or reorder frequency):
   Return JSON:
   {
       "action": "query_memory",
       "product": "<product_name>"
   }

3. Otherwise:
   Return JSON:
   {
       "action": "unknown"
   }

Choose exactly one intent. Return ONLY JSON. Never explain.
"""

class RestaurantMemoryAgent:
    """
    Agent responsible for querying and managing product preferences,
    brands, suppliers, reorder intervals, and purchase history.
    """
    
    def execute(self, message: str) -> str:
        if not message:
            return "No memory details provided."
            
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
            product = data.get("product", "").strip()
            
            if action == "set_preference" and product:
                preferred_brand = data.get("preferred_brand")
                preferred_supplier = data.get("preferred_supplier")
                
                # Filter out string representations of null
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
                
            elif action == "query_memory" and product:
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
                
                reply = [
                    f"🧠 *Grovio Restaurant Memory: {product.capitalize()}*",
                    "",
                    f"• *Preferred Brand*: {brand_str}",
                    f"• *Preferred Supplier*: {supplier_str}",
                    f"• *Average Reorder Interval*: {interval_str} (Confidence: {mem['confidence_level']})",
                    f"• *Last Purchase Date*: {last_date_str}",
                    f"• *Pending Deliveries*: {pending_str}"
                ]
                return "\n".join(reply)
                
            else:
                return (
                    "I couldn't understand your memory request. You can ask me questions like "
                    "'when did we last order milk?' or set options like 'we prefer Nandini brand for milk'."
                )
                
        except Exception as e:
            print(f"Error in RestaurantMemoryAgent execution: {e}")
            return "Sorry, I encountered an error while updating or retrieving restaurant memory."
