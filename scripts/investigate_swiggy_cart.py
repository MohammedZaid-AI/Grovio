"""
PERMANENT DIAGNOSTIC TOOL (keep in repo). Traces the full MCP shopping flow
(addresses -> search -> cart -> payment -> checkout) with SWIGGY_MCP_DEBUG on.

Deep investigation: why does update_cart return "store is currently unavailable
or closed" when search_products succeeds with the same address?

Run in your live environment (network + Swiggy auth):

    python scripts/investigate_swiggy_cart.py "coke"
    python scripts/investigate_swiggy_cart.py            # defaults to "coke"

It forces SWIGGY_MCP_DEBUG on, so every MCP call prints its request, response,
isError and any store/merchant/outlet context (via the wrapper in
integrations/swiggy/swiggy_mcp.py). On top of that it captures the two artifacts
that decide the root cause:

  A) The update_cart + search_products INPUT SCHEMAS — reveals whether
     update_cart requires a top-level storeId/outletId/merchantId that we never
     send.
  B) The STORE TIMELINE — the store context returned by search vs. what (if
     anything) update_cart/get_cart report — reveals whether search resolves an
     OPEN store but update_cart re-resolves a DIFFERENT / CLOSED one.

Everything runs on ONE SwiggyInstamart instance (one MCP session), so the
cookies/auth/session are identical across search -> cart -> get_cart.

Paste the entire output back. Do NOT change any production behaviour from this
script — it only reads and logs.
"""
import asyncio
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Force the gated instrumentation on for this run (set before/independent of the
# module's import-time read).
os.environ["SWIGGY_MCP_DEBUG"] = "1"
import integrations.swiggy.swiggy_mcp as swiggy_mcp
swiggy_mcp.SWIGGY_MCP_DEBUG = True
from integrations.swiggy.swiggy_mcp import SwiggyInstamart, _scan_context


def dump(obj):
    try:
        return json.dumps(obj, indent=2, default=str)
    except Exception:
        return repr(obj)


def banner(title):
    print("\n" + "=" * 78)
    print(title)
    print("=" * 78)


# Keys that carry the post-checkout flow (QR / UPI intent / redirect / order id).
_FLOW_KEYS = (
    "orderid", "order_id", "status", "cartotal", "carttotal",
    "upiurl", "upi_url", "intenturl", "intent_url", "deeplink", "deep_link",
    "qr", "qrcode", "qr_code", "qrstring", "qr_string",
    "redirecturl", "redirect_url", "paymenturl", "payment_url",
    "paymentlink", "payment_link", "txnid", "transactionid", "transaction_id",
    "paymentmethod", "payment_method",
)


def _scan_flow(obj, depth=0):
    """Collect any order/payment/QR/redirect fields the checkout response carries."""
    found = {}
    if depth > 6:
        return found
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k.lower() in _FLOW_KEYS and not isinstance(v, (dict, list)):
                found[k] = v
            for sk, sv in _scan_flow(v, depth + 1).items():
                found.setdefault(sk, sv)
    elif isinstance(obj, list) and obj:
        for sk, sv in _scan_flow(obj[0], depth + 1).items():
            found.setdefault(sk, sv)
    return found


async def dump_schemas(swiggy):
    banner("A) TOOL INPUT SCHEMAS (does update_cart require a store identifier?)")
    tools = await swiggy.session.list_tools()
    wanted = {"update_cart", "search_products", "get_cart", "checkout"}
    for tool in tools:
        name = getattr(tool, "name", "")
        if name in wanted:
            schema = getattr(tool, "inputSchema", None)
            required = (schema or {}).get("required") if isinstance(schema, dict) else None
            props = list(((schema or {}).get("properties") or {}).keys()) if isinstance(schema, dict) else None
            print(f"\n--- {name} ---")
            print(f"required   = {required}")
            print(f"properties = {props}")
            print(f"full schema:\n{dump(schema)}")


async def main():
    item = (sys.argv[1] if len(sys.argv) > 1 else "coke").strip()

    # ONE instance == ONE MCP session for the entire flow.
    swiggy = await SwiggyInstamart().initialize()

    await dump_schemas(swiggy)

    banner("B) get_addresses (how many? is the FIRST one stable/valid?)")
    addrs = await swiggy.get_default_address()
    address_list = (getattr(addrs, "structuredContent", None) or {}).get("addresses", [])
    print(f"address count = {len(address_list)}")
    for i, a in enumerate(address_list):
        print(f"  [{i}] id={a.get('id')} "
              f"lat={a.get('lat')} lng={a.get('lng')} "
              f"label={a.get('annotation') or a.get('name')}")
    address_id = await swiggy.get_address_id()
    print(f"get_address_id() chose -> {address_id!r}")

    banner("C) search_products (full response logged above by wrapper)")
    search = await swiggy.search_product(address_id, item)
    search_sc = getattr(search, "structuredContent", None)
    search_ctx = _scan_context(search_sc)
    print(f"search isError = {getattr(search, 'isError', None)}")
    print(f"search store context = {search_ctx or '(none at top level)'}")
    products = (search_sc or {}).get("products", []) if isinstance(search_sc, dict) else []
    if not products:
        print("No products — cannot continue.")
        return
    variant = products[0]["variations"][0]
    print(f"chosen product = {products[0].get('displayName')} "
          f"spinId={variant.get('spinId')} skuId={variant.get('skuId')}")
    # Any store id hiding on the product/variant itself?
    print(f"product-level store context = {_scan_context(products[0]) or '(none on product)'}")

    banner("D) update_cart (same address, skuId included)")
    await swiggy.clear_cart()
    payload = [{
        "spinId": variant.get("spinId"),
        "skuId": variant.get("skuId"),
        "quantity": 1,
    }]
    upd = await swiggy.update_cart(address_id, payload)
    upd_err_text = None
    content = getattr(upd, "content", None)
    if content and getattr(content[0], "text", None):
        upd_err_text = content[0].text
    print(f"update_cart isError = {getattr(upd, 'isError', None)}")
    print(f"update_cart message = {upd_err_text!r}")

    banner("E) get_cart (real server-side cart)")
    cart = await swiggy.get_cart()
    ccontent = getattr(cart, "content", None)
    print(f"get_cart isError = {getattr(cart, 'isError', None)}")
    if ccontent and getattr(ccontent[0], "text", None):
        print(f"get_cart message = {ccontent[0].text}")

    # If the cart never populated, checkout cannot succeed — stop and say so.
    cart_ok = not getattr(cart, "isError", False) and not getattr(upd, "isError", False)

    banner("F) get_payment_options (same address) — what does THIS account get?")
    pay = await swiggy.get_payment_options(address_id)
    pay_err = getattr(pay, "isError", None)
    pay_text = None
    pcontent = getattr(pay, "content", None)
    if pcontent and getattr(pcontent[0], "text", None):
        pay_text = pcontent[0].text
    print(f"get_payment_options isError = {pay_err}")
    print(f"get_payment_options message = {pay_text!r}")

    # Decide the payment method EXACTLY as documented (read from checkout schema).
    # COD group value is "Cash"; UPI needs paymentMethod="UPI" + intentApp.
    # We attempt COD first because it needs no account whitelisting.
    payment_method = "Cash"
    intent_app = None
    generate_upi_qr = False
    print(f"\nAttempting checkout with the documented COD group value: "
          f"paymentMethod={payment_method!r}")

    banner("G) checkout (same MCP client, same addressId, same cart)")
    exact_payload = {
        "addressId": address_id,
        "paymentMethod": payment_method,
    }
    if intent_app:
        exact_payload["intentApp"] = intent_app
    if generate_upi_qr:
        exact_payload["generateUPIQR"] = True
    print(f"EXACT checkout payload we send = {dump(exact_payload)}")

    chk = await swiggy.checkout(
        address_id,
        payment_method=payment_method,
        intent_app=intent_app,
        generate_upi_qr=generate_upi_qr,
    )
    chk_err = getattr(chk, "isError", None)
    chk_text = None
    kcontent = getattr(chk, "content", None)
    if kcontent and getattr(kcontent[0], "text", None):
        chk_text = kcontent[0].text
    chk_sc = getattr(chk, "structuredContent", None)
    flow = _scan_flow(chk_sc) or _scan_flow(_safe_json(chk_text))
    print(f"checkout isError  = {chk_err}")
    print(f"checkout message  = {chk_text!r}")
    print(f"checkout context  = {_scan_context(chk_sc) or '(none)'}")
    print(f"payment/QR/redirect flow = {flow or '(none returned)'}")
    print(f"checkout structuredContent:\n{dump(chk_sc)}")

    banner("STORE TIMELINE / ROOT-CAUSE SIGNALS")
    print(f"  search store context : {search_ctx or '(none)'}")
    print(f"  update_cart isError  : {getattr(upd, 'isError', None)}")
    print(f"  update_cart message  : {upd_err_text!r}")
    print(f"  cart usable          : {cart_ok}")
    print(f"  get_payment_options  : isError={pay_err} msg={pay_text!r}")
    print(f"  checkout isError     : {chk_err}")
    print(f"  checkout message     : {chk_text!r}")
    print(f"  checkout flow fields : {flow or '(none)'}")

    banner("CHECKOUT FAILURE CLASSIFICATION")
    if not cart_ok:
        print("  -> CART VALIDATION (category 3): checkout cannot proceed because "
              "the cart/add step itself failed above. Fix the cart first.")
    elif not chk_err:
        print("  -> SUCCESS: checkout returned no error. See the flow fields above "
              "for orderId / QR / redirect. No fix needed.")
    else:
        low = (chk_text or "").lower()
        if "payment" in low and ("select" in low or "method" in low or "no payment" in low):
            print("  -> PAYMENT SELECTION / METHOD (category 1/2): checkout wants a "
                  "different/valid payment method. Compare the paymentMethod enum in "
                  "the checkout schema (section A) with what we sent ('Cash') and "
                  "with get_payment_options (section F).")
        elif "cart" in low or "empty" in low:
            print("  -> CART VALIDATION (category 3): checkout rejects the cart state.")
        elif "store" in low or "unavailable" in low or "closed" in low:
            print("  -> STORE/PROTOCOL (category 4): store/context problem surfaced at "
                  "checkout — check store context in the timeline above.")
        else:
            print("  -> CHECKOUT PROTOCOL / MCP (category 4/5): unrecognised error. "
                  "Read the exact message + schema above to name the missing field.")
    print("\nDeliverables to paste back: sections A (checkout schema), F, G, and "
          "this classification block.")


def _safe_json(text):
    if not text:
        return None
    try:
        return json.loads(text)
    except Exception:
        return None


if __name__ == "__main__":
    asyncio.run(main())
