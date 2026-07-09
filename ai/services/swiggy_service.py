import json
import re

from integrations.swiggy.swiggy_mcp import SwiggyInstamart
from ai.agents.checkout_recovery_agent import checkout_recovery


class SwiggyService:
    """
    High-level wrapper around Swiggy MCP.

    All agents communicate with Swiggy
    only through this service.
    """

    def __init__(self):

        self.client = None

    # ------------------------------------
    # Initialize
    # ------------------------------------

    async def initialize(self):

        if self.client is None:

            self.client = await SwiggyInstamart().initialize()

        return self.client

    # ------------------------------------
    # Search Products
    # ------------------------------------

    async def search_products(

        self,

        product_name

    ):

        client = await self.initialize()

        return await client.get_product_options(

            product_name

        )

    # ------------------------------------
    # Cart
    # ------------------------------------

    async def clear_cart(self):

        client = await self.initialize()

        return await client.clear_cart()

    async def get_cart(self):

        client = await self.initialize()

        return await client.get_cart()

    async def build_cart(

        self,

        items

    ):

        client = await self.initialize()

        address_id = await client.get_address_id()

        payload = []

        for item in items:

            entry = {
                "spinId": item["spinId"],
                "quantity": item["quantity"],
            }

            # Swiggy's update_cart requires skuId per item; omitting it makes the
            # add silently fail and leaves the cart empty (-> "cart not found"
            # at checkout).
            sku = item.get("skuId")
            if sku:
                entry["skuId"] = sku

            payload.append(entry)

        await client.clear_cart()

        result = await client.update_cart(

            address_id,

            payload

        )

        # Surface add-to-cart failures instead of swallowing them: a failed
        # update_cart is why checkout later reports an empty/expired cart.
        if getattr(result, "isError", False):
            text = None
            content = getattr(result, "content", None)
            if content and getattr(content[0], "text", None):
                text = content[0].text
            print(f"[SwiggyService] update_cart FAILED (items not added): {text!r} "
                  f"payload={payload}")

        return await client.get_cart()

    # ------------------------------------
    # Payment Options
    # ------------------------------------

    async def get_payment_options(self):

        client = await self.initialize()

        result = await client.get_payment_options()

        if getattr(result, "isError", False):

            # Log full detail server-side to diagnose tool name / argument issues.
            raw_text = None
            content = getattr(result, "content", None)
            if content and getattr(content[0], "text", None):
                raw_text = content[0].text
            print(f"[SwiggyService] get_payment_options FAILED. "
                  f"text={raw_text!r} structured={getattr(result, 'structuredContent', None)!r}")

            return {
                "success": False,
                "message": "⚠️ We couldn't load the available payment methods right now. Please try again in a few minutes."
            }

        return self._parse_payment_options(result)

    def _parse_payment_options(self, result):
        """Normalize the Swiggy get_payment_options response into a rich list.

        Each option carries everything checkout() needs:
            {
              "label": <human text>,
              "paymentMethod": "UPI" | "Cash" | ...,   # the GROUP value
              "intentApp": <upi app id> or None,        # only for UPI apps
              "generateUPIQR": bool,                    # only for desktop QR
            }

        Per the Swiggy schema, the response exposes:
          - platforms.mobile.methods[]  -> UPI apps (id -> intentApp, group "UPI")
          - platforms.desktop.methods[] -> desktop scan-QR (generateUPIQR, group "UPI")
          - cod                          -> Cash on Delivery (group "Cash")
          - allMethods[]                 -> flat fallback list
        Returns {"success": True, "options": [...]} or a friendly error dict.
        """
        try:
            data = getattr(result, "structuredContent", None) or {}

            # Fall back to the JSON text payload if structuredContent is empty.
            if not data:
                content = getattr(result, "content", None)
                if content and getattr(content[0], "text", None):
                    parsed = json.loads(content[0].text)
                    data = parsed if isinstance(parsed, dict) else {}

            # The real payload may be wrapped under a "data" key.
            if isinstance(data.get("data"), dict):
                data = data["data"]

            options = []

            platforms = data.get("platforms") or {}

            # UPI mobile apps
            for m in ((platforms.get("mobile") or {}).get("methods") or []):
                app_id = m.get("id")
                label = m.get("displayName") or m.get("name") or m.get("label") or app_id
                if app_id:
                    options.append({
                        "label": label,
                        "paymentMethod": "UPI",
                        "intentApp": app_id,
                        "generateUPIQR": False,
                    })

            # Desktop scan-QR (UPI)
            for m in ((platforms.get("desktop") or {}).get("methods") or []):
                label = m.get("displayName") or m.get("name") or m.get("label") or "Scan QR to pay (UPI)"
                options.append({
                    "label": label,
                    "paymentMethod": "UPI",
                    "intentApp": None,
                    "generateUPIQR": True,
                })

            # Cash on Delivery
            cod = data.get("cod")
            if cod:
                label = "Cash on Delivery"
                if isinstance(cod, dict):
                    label = cod.get("displayName") or cod.get("label") or label
                options.append({
                    "label": label,
                    "paymentMethod": "Cash",
                    "intentApp": None,
                    "generateUPIQR": False,
                })

            # Flat fallback list (allMethods / options / methods)
            if not options:
                flat = None
                for key in ("allMethods", "paymentOptions", "options", "methods"):
                    if isinstance(data.get(key), list):
                        flat = data[key]
                        break
                for m in (flat or []):
                    if isinstance(m, str):
                        options.append({"label": m, "paymentMethod": m, "intentApp": None, "generateUPIQR": False})
                        continue
                    group = m.get("paymentMethod") or m.get("group") or m.get("type") or m.get("id")
                    label = m.get("displayName") or m.get("name") or m.get("label") or group
                    if group:
                        options.append({
                            "label": label,
                            "paymentMethod": group,
                            "intentApp": m.get("intentApp") or (m.get("id") if str(group).upper() == "UPI" else None),
                            "generateUPIQR": bool(m.get("generateUPIQR")),
                        })

            if not options:
                print(f"[SwiggyService] No payment options parsed from result: {data}")
                return {
                    "success": False,
                    "message": "We couldn't load the available payment methods right now. Please try again in a few minutes."
                }

            return {"success": True, "options": options}

        except Exception as e:
            print(f"[SwiggyService] Exception parsing payment options: {e}")
            return {
                "success": False,
                "message": "We couldn't load the available payment methods right now. Please try again in a few minutes."
            }

    # ------------------------------------
    # Checkout
    # ------------------------------------

    async def checkout(self, payment_method=None, intent_app=None, generate_upi_qr=False):

        client = await self.initialize()

        address_id = await client.get_address_id()

        result = await client.checkout(

            address_id,

            payment_method=payment_method,

            intent_app=intent_app,

            generate_upi_qr=generate_upi_qr

        )

        if getattr(

            result,

            "isError",

            False

        ):

            return await self._parse_error(

                result

            )

        return self._parse_success(

            result

        )

    # ------------------------------------
    # Success
    # ------------------------------------

    # Candidate key names per field — Swiggy's payload is camelCase, but we stay
    # tolerant of snake_case / alternates so a minor schema change won't break us.
    _FIELD_KEYS = {
        "order_id": ("orderId", "order_id", "id"),
        "status": ("status", "orderStatus"),
        "payment": ("paymentMethod", "payment_method", "payment"),
        "total": ("cartTotal", "orderTotal", "grandTotal", "total", "amount"),
        "bridge_url": ("bridgeUrl", "bridge_url"),
        "upi_intent_url": ("upiIntentUrl", "upi_intent_url", "intentUrl"),
        "paas_id": ("paasId", "paas_id"),
        "transaction_id": ("transactionId", "transaction_id", "txnId"),
        "redirect_url": ("redirectUrl", "redirect_url"),
        "qr": ("QR", "qr", "qrCode", "qrString", "qr_code"),
    }

    def _result_text(self, result):
        """Best-effort human-readable text from an MCP result (or None)."""
        content = getattr(result, "content", None)
        if content and getattr(content[0], "text", None):
            return content[0].text
        return None

    def _extract_order_fields(self, data):
        """Pull the known order fields out of a dict, looking at both the top
        level and a nested `data` object (Swiggy wraps the order under `data`)."""
        sources = [data]
        inner = data.get("data") if isinstance(data.get("data"), dict) else None
        if inner:
            sources.append(inner)

        def pick(keys):
            for src in sources:
                for k in keys:
                    v = src.get(k)
                    if v not in (None, ""):
                        return v
            return None

        return {field: pick(keys) for field, keys in self._FIELD_KEYS.items()}

    def _extract_from_text(self, text):
        """Regex fallback for a human-readable success message / widget notice.
        Anything not found stays None — we never raise here."""
        text = text or ""

        def find(pattern):
            m = re.search(pattern, text, re.IGNORECASE)
            return m.group(1).strip() if m else None

        return {
            "order_id": find(r"order\s*id[\s:#\-]*([A-Za-z0-9\-]+)"),
            "status": find(r"status[\s:#\-]*([A-Za-z_]+)"),
            "payment": find(r"payment(?:\s*method)?[\s:#\-]*([A-Za-z ]+?)(?:\.|,|\n|$)"),
            "total": find(r"(?:total|amount)[\s:₹rs\.]*([0-9]+(?:\.[0-9]+)?)"),
            "bridge_url": find(r"(https?://\S*bridge\S*)"),
            "upi_intent_url": find(r"(upi://\S+)"),
            "paas_id": None,
            "transaction_id": find(r"transaction\s*id[\s:#\-]*([A-Za-z0-9\-]+)"),
            "redirect_url": find(r"(https?://\S+)"),
            "qr": None,
        }

    def _success_payload(self, fields, message, clean):
        """Build the success dict. order_placed is ALWAYS True here: _parse_success
        is only ever called when the MCP checkout did NOT error (order confirmed),
        so the shopping session must be closed regardless of how parseable the
        body was. `clean` distinguishes a fully-parsed order (success=True) from a
        confirmed-but-unparsed one (success=False -> 'submitted' message)."""
        payload = {
            "success": bool(clean),
            "order_placed": True,
            "message": message or "Order placed successfully.",
        }
        payload.update(fields)
        if not payload.get("status"):
            payload["status"] = "CONFIRMED"
        return payload

    def _parse_success(self, result):
        # NOTE: checkout() only calls this when result.isError is False, i.e. the
        # order is already CONFIRMED. This method therefore NEVER throws and always
        # reports order_placed=True so the conversation state is cleaned up.
        try:
            if result is None:
                print("[SwiggyService] checkout result is None.")
                return {
                    "success": False,
                    "order_placed": False,
                    "code": "EMPTY_RESPONSE",
                    "message": "⚠️ We couldn't reach the store to place your order. Please try again in a few minutes.",
                }

            structured = getattr(result, "structuredContent", None)
            text = self._result_text(result)

            # 1. structuredContent is authoritative — use it directly and do NOT
            #    json.loads the human-readable text (that was the parse bug).
            if isinstance(structured, dict) and structured:
                fields = self._extract_order_fields(structured)
                return self._success_payload(fields, structured.get("message"), clean=True)

            # 2. No structuredContent: only attempt json.loads if the text really
            #    looks like JSON (starts with '{' or '[').
            if text and text.lstrip()[:1] in ("{", "["):
                try:
                    data = json.loads(text)
                    if isinstance(data, dict):
                        fields = self._extract_order_fields(data)
                        return self._success_payload(fields, data.get("message"), clean=True)
                except Exception as e:
                    print(f"[SwiggyService] text looked like JSON but did not parse: {e}")

            # 3. Human-readable message / widget notice: regex/text fallback. The
            #    order is placed (isError was False); success reflects whether we
            #    could recover concrete details.
            fields = self._extract_from_text(text)
            clean = bool(fields.get("order_id") or fields.get("status"))
            return self._success_payload(fields, (text.strip() if text else None), clean=clean)

        except Exception as e:
            # Never throw: the order was accepted. Report it placed so the session
            # is closed (prevents the stale "reply with a number" state).
            print(f"[SwiggyService] _parse_success unexpected error: {e}")
            return {
                "success": False,
                "order_placed": True,
                "status": "CONFIRMED",
                "order_id": None,
                "message": "Order placed successfully.",
            }

    # ------------------------------------
    # Error
    # ------------------------------------

    # User-facing messages per error code. The raw Swiggy error (which can
    # contain internal Report IDs like "ERR-MRBXZQHQ...") is NEVER shown to
    # the user — it is only logged server-side. See fix for the raw-error leak.
    _FRIENDLY_ERROR_MESSAGES = {
        "LIMIT_EXCEEDED": "⚠️ One or more items exceed the maximum quantity allowed per order. Please reduce the quantity and try again.",
        "OUT_OF_STOCK": "⚠️ One or more items are out of stock right now. Please try again later or pick an alternative.",
        "PARTIAL_AVAILABILITY": "⚠️ Some items are only partially available. I can help you adjust the order.",
        "STORE_UNAVAILABLE": "⚠️ The store is currently unavailable. Please try again in a little while.",
        "NO_PAYMENT_METHOD": "⚠️ Please choose a payment method before placing the order.",
        "UNKNOWN": "⚠️ We couldn't complete the checkout right now. Please try again in a few minutes.",
    }

    async def _parse_error(

        self,

        result

    ):

        error = ""

        if result.content:

            error = result.content[0].text

        # Log the raw error server-side for debugging (never sent to the user).
        print(f"[SwiggyService] Raw checkout error from Swiggy MCP: {error}")

        decision = await checkout_recovery.execute(

            error

        )

        error_lower = error.lower()

        code = "UNKNOWN"

        if "Max Per Item Quantity Limit" in error:

            code = "LIMIT_EXCEEDED"

        elif "Out of Stock" in error:

            code = "OUT_OF_STOCK"

        elif "partially available" in error_lower:

            code = "PARTIAL_AVAILABILITY"

        elif "store is currently unavailable" in error_lower:

            code = "STORE_UNAVAILABLE"

        elif "payment method" in error_lower:

            code = "NO_PAYMENT_METHOD"

        return {

            "success": False,

            "code": code,

            # Clean, user-friendly message only. Raw error kept separately for logs.
            "message": self._FRIENDLY_ERROR_MESSAGES.get(code, self._FRIENDLY_ERROR_MESSAGES["UNKNOWN"]),

            "raw_error": error,

            "decision": decision

        }