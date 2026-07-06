import json

from core.llm import llm


# Plausibility thresholds for suggestion sanity checks.
# If a suggested quantity exceeds this, the suggestion is suppressed
# (the extraction is likely fundamentally wrong, not a simple digit-merge).
MAX_PLAUSIBLE_QTY_SALES_BILL = 50
MAX_PLAUSIBLE_QTY_SUPPLIER_INVOICE = 500


class InvoiceExtractor:

    """
    Converts raw invoice text
    into structured JSON.
    """

    def __init__(self):

        pass

    def verify_and_flag_payload(self, invoice):
        items = invoice.get("items", [])
        if not items:
            return invoice

        def safe_float(val):
            if val is None or val == "":
                return None
            try:
                s = str(val).replace("₹", "").replace(",", "").strip()
                return float(s)
            except ValueError:
                return None

        # Build raw items list and clean up basic numeric formats
        processed_items = []
        for item in items:
            processed_items.append({
                "product": item.get("product", ""),
                "quantity": safe_float(item.get("quantity")),
                "unit": item.get("unit", ""),
                "unit_price": safe_float(item.get("unit_price")),
                "total": safe_float(item.get("total")),
                "is_inconsistent": False,
                "suggested_correction": None
            })

        # Calculate expected values and look for inconsistencies
        # 1. Look for known price correction pattern (digit prefix)
        corrected_prices_map = {}
        for item in processed_items:
            qty = item["quantity"]
            price = item["unit_price"]
            total = item["total"]
            
            # Check prefix on both price and total (Case A)
            if qty is not None and qty > 0 and price is not None and total is not None:
                price_str = str(int(price))
                total_str = str(int(total))
                if abs(qty * price - total) > 1.0:
                    item["is_inconsistent"] = True
                    # Try correction suggestion
                    suggestion = {"quantity": qty, "unit_price": price, "total": total}
                    has_correction = False
                    if len(price_str) > 1 and len(total_str) > 1 and price_str[0] == total_str[0]:
                        stripped_price = safe_float(price_str[1:])
                        stripped_total = safe_float(total_str[1:])
                        if stripped_price is not None and stripped_total is not None:
                            if abs(qty * stripped_price - stripped_total) < 5.0:
                                suggestion["unit_price"] = stripped_price
                                suggestion["total"] = stripped_total
                                corrected_prices_map[item["product"]] = stripped_price
                                has_correction = True
                    # Case B: Prepended digit prefix on price only
                    elif len(price_str) > 1:
                        stripped_price = safe_float(price_str[1:])
                        if stripped_price is not None and abs(qty * stripped_price - total) < 5.0:
                            suggestion["unit_price"] = stripped_price
                            corrected_prices_map[item["product"]] = stripped_price
                            has_correction = True
                    # Case C: Prepended digit prefix on total only
                    elif len(total_str) > 1:
                        stripped_total = safe_float(total_str[1:])
                        if stripped_total is not None and abs(qty * price - stripped_total) < 5.0:
                            suggestion["total"] = stripped_total
                            has_correction = True
                    
                    if has_correction:
                        item["suggested_correction"] = suggestion

        # 2. Map prices to other identical products
        for item in processed_items:
            qty = item["quantity"]
            price = item["unit_price"]
            
            if qty is not None and price is not None and item["total"] is None:
                item["is_inconsistent"] = True
                price_str = str(int(price))
                qty_str = str(int(qty))
                suggestion = {"quantity": qty, "unit_price": price, "total": qty * price}
                
                # Check for prepended quantity
                if price_str.startswith(qty_str) and len(price_str) > 2:
                    stripped_price = safe_float(price_str[len(qty_str):])
                    if stripped_price is not None and stripped_price < 1000:
                        suggestion["unit_price"] = stripped_price
                        suggestion["total"] = qty * stripped_price
                        corrected_prices_map[item["product"]] = stripped_price
                elif item["product"] in corrected_prices_map:
                    resolved_price = corrected_prices_map[item["product"]]
                    suggestion["unit_price"] = resolved_price
                    suggestion["total"] = qty * resolved_price
                item["suggested_correction"] = suggestion

        # 3. Solve for single missing total
        total_amount = safe_float(invoice.get("total_amount", 0.0))
        unresolved_indices = [
            i for i, item in enumerate(processed_items) 
            if item["total"] is None and (not item["suggested_correction"] or item["suggested_correction"].get("total") is None)
        ]
        
        if len(unresolved_indices) == 1 and total_amount > 0:
            idx = unresolved_indices[0]
            item = processed_items[idx]
            item["is_inconsistent"] = True
            
            # Sum up other totals (use suggestions if present)
            other_totals = []
            for i, it in enumerate(processed_items):
                if i == idx:
                    continue
                if it["total"] is not None and not it["is_inconsistent"]:
                    other_totals.append(it["total"])
                elif it["suggested_correction"] and it["suggested_correction"].get("total") is not None:
                    other_totals.append(it["suggested_correction"]["total"])
                else:
                    other_totals.append(it["total"] or 0.0)
            
            other_total_sum = sum(other_totals)
            diff_no_tax = total_amount - other_total_sum
            diff_with_tax = (total_amount / 1.05) - other_total_sum
            
            solved_total = None
            if diff_no_tax > 0 and (item["quantity"] is None or (item["quantity"] > 0 and abs(diff_no_tax / item["quantity"] - (item["unit_price"] or 1.0)) < 100)):
                solved_total = diff_no_tax
            elif diff_with_tax > 0:
                solved_total = diff_with_tax
                
            if solved_total is not None and solved_total > 0:
                qty = item["quantity"] or 1.0
                suggestion = {
                    "quantity": item["quantity"],
                    "unit_price": round(solved_total / qty, 2) if item["quantity"] else None,
                    "total": round(solved_total, 2)
                }
                item["suggested_correction"] = suggestion

        # 4. Standard validation check: if quantity * price != total and not already marked inconsistent
        for item in processed_items:
            qty = item["quantity"]
            price = item["unit_price"]
            total = item["total"]
            
            if qty is None or price is None or total is None or (qty is not None and qty > 0 and (price == 0.0 or total == 0.0)):
                item["is_inconsistent"] = True
                if not item["suggested_correction"]:
                    suggestion = {"quantity": qty, "unit_price": price if price != 0.0 else None, "total": total if total != 0.0 else None}
                    if qty is not None and price is not None and price > 0:
                        suggestion["total"] = qty * price
                    elif qty is not None and total is not None and total > 0:
                        suggestion["unit_price"] = round(total / qty, 2)
                    item["suggested_correction"] = suggestion
            elif abs(qty * price - total) > 1.0:
                item["is_inconsistent"] = True
                if not item["suggested_correction"]:
                    suggestion = {
                        "quantity": qty,
                        "unit_price": price,
                        "total": total
                    }
                    if qty is not None and qty > 0:
                        solved_price = round(total / qty, 2)
                        price_digits = "".join(c for c in str(price) if c.isdigit())
                        solved_digits = "".join(c for c in str(solved_price) if c.isdigit())
                        if price_digits in solved_digits or solved_digits in price_digits:
                            suggestion["unit_price"] = solved_price
                        else:
                            suggestion["total"] = qty * price
                    else:
                        suggestion["total"] = qty * price if qty is not None else None
                    item["suggested_correction"] = suggestion

        # 5. Plausibility guard: suppress suggestions with absurd quantities
        doc_type = invoice.get("doc_type", "SALES_BILL")
        max_qty = MAX_PLAUSIBLE_QTY_SUPPLIER_INVOICE if doc_type == "SUPPLIER_INVOICE" else MAX_PLAUSIBLE_QTY_SALES_BILL
        for item in processed_items:
            sug = item.get("suggested_correction")
            if sug:
                sug_qty = sug.get("quantity")
                if sug_qty is not None and sug_qty > max_qty:
                    # Quantity is implausibly large — the extraction is likely
                    # fundamentally wrong (e.g. column shift), not a simple
                    # digit-merge.  Remove suggestion to force manual entry.
                    item["suggested_correction"] = None
            # Also check the raw extracted quantity itself
            raw_qty = item.get("quantity")
            if raw_qty is not None and raw_qty > max_qty:
                item["is_inconsistent"] = True
                if item.get("suggested_correction") is None:
                    # No suggestion — just flag it for manual review
                    item["suggested_correction"] = None

        invoice["items"] = processed_items
        invoice["total_amount"] = total_amount
        return invoice

    def extract(self, invoice_text):

        prompt = f"""
You are an expert document extraction AI.
We process two types of documents:
1. SUPPLIER_INVOICE: Billed by suppliers/vendors to the restaurant for raw ingredients (e.g. Milk, Butter, Bread, Oil, Veg).
2. SALES_BILL: Billed by the restaurant to customers for dishes/meals sold (e.g. Chicken Steak, Veg Salad, Butter Toast).

Classify the document type under "doc_type" as either "SUPPLIER_INVOICE" or "SALES_BILL".

Extract EVERY single line item listed in the document.
Return ONLY valid JSON.

Schema:
{{
    "doc_type": "SUPPLIER_INVOICE" | "SALES_BILL",
    "supplier": "", // For SUPPLIER_INVOICE (e.g. ABC Dairy)
    "invoice_number": "", // invoice number or bill number
    "date": "YYYY-MM-DD",
    "items": [
        {{
            "product": "", // For SUPPLIER_INVOICE, raw ingredient name. For SALES_BILL, prepared dish/item name.
            "quantity": null, // Quantity of item. If missing or unparseable, set to null.
            "unit": "", // unit of measurement (e.g. packets, kg, liters, loaves, unit)
            "unit_price": 0.0,
            "total": 0.0
        }}
    ],
    "total_amount": 0.0
}}

Rules:
1. **Duplicate Item Names**: If the same product or dish name appears multiple times in the document (even at different prices or quantities), you MUST extract each occurrence as a separate object in the "items" list. Do NOT merge them, combine their quantities, or skip any of them.
2. **Column Misalignment and Merged Numbers**: OCR text sometimes merges quantity and unit price together due to close column spacing (e.g., "2 499" or "2 399"). In these cases, recognize that the first digit is the quantity ("2") and the rest is the unit price ("499" or "399"). Do NOT extract the unit price as "2499" or "2399".
3. **No Recalculations**: Extract all numbers (quantities, unit prices, totals) EXACTLY as they are written in the document text. Do NOT perform any arithmetic corrections, division, multiplication, or mathematical adjustments yourself.
4. **Stray Column Characters**: Ensure that row index numbers (e.g. "1", "2", "3") or quantity numbers are not prepended to product names, unit prices, or total amounts.
5. If a quantity is ambiguous or unparseable, set it to null. If a field is missing, use null or "".
6. **Missing Quantity Column**: If the document header shows columns like "Item / Qty / Rate / Total" but only TWO numbers appear per item row (not three), then the quantity column was likely not captured by OCR. In this case, the two visible numbers are "Rate" (unit_price) and "Total" — NOT "Quantity" and "Unit Price". Set quantity to null, assign the first number to unit_price and the second to total.
7. **Multi-Line Wrapped Item Names**: OCR text may split a single item name across multiple printed lines (e.g. "HYDERABADI MURG" on one line, "BIRYANI" on a later line, with numbers in between). Rejoin these fragments into one product name. Clues: a text-only line appearing immediately after a number row likely belongs to the previous item name, not a new item.

Document Text:
{invoice_text}
"""

        response = llm.chat(
            system="""
You are an expert invoice and sales bill extraction assistant.
Always return valid JSON only. Keep your chain of thought concise. Do not perform deep mathematical calculations or analysis in your reasoning.
""",
            user=prompt,
            temperature=0
        )

        try:
            # Strip markdown code blocks if present in response
            clean_res = response.strip()
            if clean_res.startswith("```"):
                # Remove starting backticks and optional language identifier (e.g. ```json)
                clean_res = clean_res.split("\n", 1)[1]
                if clean_res.endswith("```"):
                    clean_res = clean_res.rsplit("\n", 1)[0]
                clean_res = clean_res.strip()
            
            data = json.loads(clean_res)
            return self.verify_and_flag_payload(data)

        except Exception:

            return {

                "doc_type": "SUPPLIER_INVOICE",

                "supplier": "",

                "invoice_number": "",

                "date": "",

                "items": [],

                "total_amount": 0,

                "error": response

            }


if __name__ == "__main__":

    sample_invoice = """

ABC Dairy

Invoice No INV-001

26 June 2026

Milk      20 Packets    ₹38    ₹760

Butter    5 Packs       ₹55    ₹275

Total ₹1035

"""

    extractor = InvoiceExtractor()

    result = extractor.extract(

        sample_invoice

    )

    from pprint import pprint

    pprint(result)