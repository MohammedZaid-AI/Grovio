from fastapi import APIRouter, Form, Request, HTTPException, UploadFile, File
from fastapi.responses import Response, HTMLResponse, FileResponse, RedirectResponse, JSONResponse
from twilio.twiml.messaging_response import MessagingResponse
from twilio.request_validator import RequestValidator
import os
import jwt
import datetime
import uuid
import hmac
import collections

from core.logger import logger

# Startup check: fail if JWT_SECRET is not in environment or is too weak
JWT_SECRET = os.getenv("JWT_SECRET")
if not JWT_SECRET:
    raise RuntimeError("JWT_SECRET environment variable is NOT set. The server cannot start.")
if len(JWT_SECRET.encode("utf-8")) < 32:
    raise RuntimeError("JWT_SECRET must be at least 32 bytes long to ensure secure HMAC-SHA256 signing.")

from backend.conversation_engine import engine
from ai.invoice.pipeline import InvoicePipeline

JWT_ALGORITHM = "HS256"

def create_access_token(data: dict):
    expire = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(hours=24)
    to_encode = data.copy()
    to_encode.update({"exp": expire})
    encoded_jwt = jwt.encode(to_encode, JWT_SECRET, algorithm=JWT_ALGORITHM)
    return encoded_jwt

def check_authenticated(request: Request) -> bool:
    token = request.cookies.get("session_token")
    if not token:
        return False
    try:
        jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
        return True
    except jwt.PyJWTError:
        return False

def get_current_user(request: Request):
    token = request.cookies.get("session_token")
    if not token:
        raise HTTPException(status_code=401, detail="Not authenticated")
    try:
        payload = jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
        return payload
    except jwt.PyJWTError:
        raise HTTPException(status_code=401, detail="Invalid token")

router = APIRouter()

pipeline = InvoicePipeline()

# -------------------------------------------------------
# Login Rate Limiting (in-memory, single-process)
# -------------------------------------------------------
# SECURITY: prevents unbounded password-guessing against /admin/login.
# Keyed by client IP. Not distributed (fine for this single-instance
# SQLite-backed deployment; see CLAUDE.md known limitations).
LOGIN_MAX_ATTEMPTS = 5
LOGIN_LOCKOUT_SECONDS = 15 * 60  # 15 minutes

_login_failures = collections.defaultdict(list)  # ip -> [failure timestamps]


def _client_ip(request: Request) -> str:
    return request.client.host if request.client else "unknown"


def _is_locked_out(ip: str) -> bool:
    now = datetime.datetime.now(datetime.timezone.utc)
    cutoff = now - datetime.timedelta(seconds=LOGIN_LOCKOUT_SECONDS)
    _login_failures[ip] = [t for t in _login_failures[ip] if t > cutoff]
    return len(_login_failures[ip]) >= LOGIN_MAX_ATTEMPTS


def _record_login_failure(ip: str):
    _login_failures[ip].append(datetime.datetime.now(datetime.timezone.utc))


def _clear_login_failures(ip: str):
    _login_failures.pop(ip, None)


# -------------------------------------------------------
# Generic Error Responses (SECURITY: L-2)
# -------------------------------------------------------
# Never return raw exception text to the client — it can leak schema,
# file paths, or other internals. Log the full exception server-side
# (with traceback) and hand back a safe, generic message instead.
GENERIC_ERROR_MESSAGE = "An internal error occurred. Please try again or contact support."


def _server_error(e: Exception, context: str) -> dict:
    logger.error(f"{context}: {e}", exc_info=True)
    return {"success": False, "message": GENERIC_ERROR_MESSAGE}


# -------------------------------------------------------
# Debug Artifacts (SECURITY: L-3)
# -------------------------------------------------------
# Full message content and raw OCR text can contain customer/financial data.
# Only print/persist it when DEBUG is explicitly enabled (default: off).
def _debug_enabled() -> bool:
    return os.getenv("DEBUG", "").strip().lower() in ("1", "true", "yes")


def split_message(text: str, max_length: int = 1500) -> list:
    if len(text) <= max_length:
        return [text]
    
    parts = []
    paragraphs = text.split("\n")
    current_part = []
    current_length = 0
    
    for paragraph in paragraphs:
        para_len = len(paragraph) + (1 if current_part else 0)
        
        if current_length + para_len > max_length:
            if current_part:
                parts.append("\n".join(current_part))
                current_part = []
                current_length = 0
            
            if len(paragraph) > max_length:
                words = paragraph.split(" ")
                current_word_part = []
                word_part_len = 0
                for word in words:
                    added_len = len(word) + (1 if current_word_part else 0)
                    if word_part_len + added_len > max_length:
                        if current_word_part:
                            parts.append(" ".join(current_word_part))
                        current_word_part = [word]
                        word_part_len = len(word)
                    else:
                        current_word_part.append(word)
                        word_part_len += added_len
                if current_word_part:
                    current_part.append(" ".join(current_word_part))
                    current_length += len(current_part[-1])
            else:
                current_part.append(paragraph)
                current_length = len(paragraph)
        else:
            current_part.append(paragraph)
            current_length += para_len
            
    if current_part:
        parts.append("\n".join(current_part))
        
    return parts

# -------------------------------------------------------
# Twilio WhatsApp Response
# -------------------------------------------------------

def whatsapp_reply(message: str or list):

    if _debug_enabled():
        print(f"\n[DEBUG_FINAL_OUTPUT] {repr(message)}\n")

    twiml = MessagingResponse()

    if isinstance(message, list):
        for msg in message:
            twiml.message(msg)
    else:
        twiml.message(message)

    return Response(

        content=str(twiml),

        media_type="application/xml"

    )


# -------------------------------------------------------
# WhatsApp Webhook
# -------------------------------------------------------

@router.post("/webhook")
async def webhook(

    request: Request,

    Body: str = Form(""),

    NumMedia: int = Form(0),

    MediaUrl0: str = Form(None),

    MediaContentType0: str = Form(None),

    From: str = Form("")

):

    # -------------------------------------------------------
    # Twilio Signature Verification
    # -------------------------------------------------------
    # SECURITY (M-2): fail CLOSED. A missing/blank TWILIO_AUTH_TOKEN must
    # never silently skip verification — that would let anyone POST forged
    # WhatsApp messages straight into the conversation engine.
    auth_token = os.getenv("TWILIO_AUTH_TOKEN")
    if not auth_token:
        print("[SECURITY] Webhook rejected: TWILIO_AUTH_TOKEN is not configured on the server.")
        raise HTTPException(status_code=500, detail="Webhook is not configured correctly.")

    validator = RequestValidator(auth_token)
    signature = request.headers.get("x-twilio-signature", "")
    proto = request.headers.get("x-forwarded-proto", "http")
    host = request.headers.get("x-forwarded-host") or request.headers.get("host") or "localhost:8000"
    url = f"{proto}://{host}{request.url.path}"

    form_data = await request.form()
    params = dict(form_data)

    if not validator.validate(url, params, signature):
        print("🚫 Webhook Signature Verification Failed!")
        raise HTTPException(status_code=403, detail="Invalid Twilio signature")

    print("\n" + "=" * 70)
    print("📩 Incoming WhatsApp Message")
    print("From :", From)
    print("Body :", Body)
    print("Media:", NumMedia)
    print("=" * 70)

    # -------------------------------------------------------
    # Document Ingestion & Classification Staging (Redirected to Web Dashboard)
    # -------------------------------------------------------
    if NumMedia > 0:
        proto = request.headers.get("x-forwarded-proto", "http")
        host = request.headers.get("x-forwarded-host") or request.headers.get("host") or "localhost:8000"
        dashboard_url = f"{proto}://{host}/admin"
        
        reply = (
            "📋 *Grovio Document Upload*\n\n"
            "Document uploads (invoices and sales bills) are now managed via the Web Admin Dashboard. "
            "Please upload your files and confirm them here:\n"
            f"{dashboard_url}"
        )
        return whatsapp_reply(reply)

    # -------------------------------------------------------
    # Conversation Engine
    # -------------------------------------------------------

    try:

        reply = await engine.process(

            phone=From,

            message=Body

        )

        if not reply:

            reply = "Sorry, I couldn't generate a response."

        parts = split_message(reply, max_length=1500)

        print("\n" + "=" * 70)
        print("Reply Sent")
        for i, part in enumerate(parts):
            print(f"--- Part {i+1} ---")
            print(part)
        print("=" * 70)

        return whatsapp_reply(parts)

    except Exception as e:

        print("\nConversation Error\n")

        print(e)

        return whatsapp_reply(

            "❌ Grovio encountered an unexpected error."

        )


# -------------------------------------------------------
# Admin Dashboard Routes
# -------------------------------------------------------

@router.get("/admin/login")
def get_login_page(request: Request):
    if check_authenticated(request):
        return RedirectResponse(url="/admin", status_code=303)
    return FileResponse("backend/pages/login.html")

@router.get("/admin")
def get_dashboard(request: Request):
    if not check_authenticated(request):
        return RedirectResponse(url="/admin/login", status_code=303)
    return FileResponse("backend/pages/dashboard.html")

@router.post("/admin/login")
async def login(request: Request, password: str = Form(...)):
    expected_password = os.getenv("DASHBOARD_PASSWORD")
    if not expected_password:
        return JSONResponse(status_code=401, content={"success": False, "message": "Dashboard password is not configured on server."})

    ip = _client_ip(request)

    # SECURITY (M-4): block brute-force password guessing.
    if _is_locked_out(ip):
        return JSONResponse(
            status_code=429,
            content={"success": False, "message": f"Too many failed login attempts. Try again in {LOGIN_LOCKOUT_SECONDS // 60} minutes."}
        )

    # SECURITY (M-4): constant-time comparison to avoid timing side-channels.
    # Compare as bytes so non-ASCII passwords don't raise in hmac.compare_digest.
    if hmac.compare_digest(password.encode("utf-8"), expected_password.encode("utf-8")):
        _clear_login_failures(ip)
        token = create_access_token({"sub": "admin"})
        response = JSONResponse(content={"success": True})
        response.set_cookie(
            key="session_token",
            value=token,
            httponly=True,
            secure=True,      # SECURITY (M-1): never send over plain HTTP
            samesite="lax",
            max_age=24*3600
        )
        return response

    _record_login_failure(ip)
    return JSONResponse(status_code=401, content={"success": False, "message": "Incorrect password"})

@router.post("/admin/logout")
def logout():
    response = JSONResponse(content={"success": True})
    response.delete_cookie("session_token")
    return response

_UPLOAD_EXTENSIONS = {
    "application/pdf": ".pdf",
    "image/jpeg": ".jpg",
    "image/jpg": ".jpg",
    "image/png": ".png",
}


async def process_web_upload(file: UploadFile, doc_type: str):
    # 1. Type validation
    if file.content_type not in _UPLOAD_EXTENSIONS:
        raise HTTPException(status_code=400, detail="Invalid file type. Only PDF and JPEG/PNG images are allowed.")

    # 2. Size validation (max 10MB)
    max_size = 10 * 1024 * 1024
    contents = await file.read()
    if len(contents) > max_size:
        raise HTTPException(status_code=400, detail="File too large. Maximum size is 10MB.")

    # Reset cursor after reading
    await file.seek(0)

    # 3. Create temp file path.
    # SECURITY (M-3): never build the path from the client-supplied filename
    # (path traversal risk, e.g. "../../../evil.py"). The extension is derived
    # solely from the already-whitelisted content_type — it's also what
    # parser.extract_text() branches on (".pdf" vs image), so this is both
    # safer and functionally correct.
    os.makedirs("downloads", exist_ok=True)
    ext = _UPLOAD_EXTENSIONS[file.content_type]
    temp_filename = f"web_{uuid.uuid4().hex}{ext}"
    temp_filepath = os.path.join("downloads", temp_filename)

    try:
        # Save temp file
        with open(temp_filepath, "wb") as f:
            f.write(contents)

        # 4. Parse local file
        parsed = pipeline.parser.parse_local(temp_filepath, file.content_type)
        
        # Save raw OCR text for debug (dev-only: this can contain
        # customer/financial data, so never persist it in production).
        if _debug_enabled():
            debug_dir = os.path.join("downloads", "ocr_debug")
            os.makedirs(debug_dir, exist_ok=True)
            debug_filename = f"raw_ocr_{doc_type}_{uuid.uuid4().hex}.txt"
            debug_filepath = os.path.join(debug_dir, debug_filename)
            with open(debug_filepath, "w", encoding="utf-8") as f_debug:
                f_debug.write(parsed.get("text", ""))
            print(f"[DEBUG] Raw OCR text saved to: {debug_filepath}")

        # Extract structured JSON
        invoice = pipeline.extractor.extract(parsed["text"])

        # Check for extraction failure
        if "error" in invoice and invoice["error"]:
            raise HTTPException(status_code=400, detail=f"LLM Extraction failed: {invoice['error']}")
        if not invoice.get("items"):
            raise HTTPException(status_code=400, detail="LLM Extraction failed: No line items could be parsed. Please verify receipt image quality and try again.")

        # Set doc_type
        invoice["doc_type"] = doc_type

        # 5. Save pending document
        from db import save_pending_document
        unique_phone = f"web_{uuid.uuid4().hex}"
        doc_id = save_pending_document(phone=unique_phone, doc_type=doc_type, payload=invoice)

        return {
            "success": True,
            "id": doc_id,
            "invoice": invoice
        }
    finally:
        # 6. Always clean up temp file
        if os.path.exists(temp_filepath):
            os.remove(temp_filepath)

@router.post("/admin/upload/sales-bill")
async def upload_sales_bill(request: Request, file: UploadFile = File(...)):
    try:
        get_current_user(request)
    except HTTPException:
        return JSONResponse(status_code=401, content={"success": False, "message": "Not authenticated"})

    try:
        result = await process_web_upload(file, "SALES_BILL")
        return result
    except HTTPException as e:
        return JSONResponse(status_code=e.status_code, content={"success": False, "message": e.detail})
    except Exception as e:
        return JSONResponse(status_code=500, content=_server_error(e, "upload_sales_bill"))

@router.post("/admin/upload/grocery-invoice")
async def upload_grocery_invoice(request: Request, file: UploadFile = File(...)):
    try:
        get_current_user(request)
    except HTTPException:
        return JSONResponse(status_code=401, content={"success": False, "message": "Not authenticated"})

    try:
        result = await process_web_upload(file, "SUPPLIER_INVOICE")
        return result
    except HTTPException as e:
        return JSONResponse(status_code=e.status_code, content={"success": False, "message": e.detail})
    except Exception as e:
        return JSONResponse(status_code=500, content=_server_error(e, "upload_grocery_invoice"))

@router.get("/admin/pending-documents")
def get_pending_documents(request: Request):
    try:
        get_current_user(request)
    except HTTPException:
        return JSONResponse(status_code=401, content={"success": False, "message": "Not authenticated"})

    from db import get_all_pending_documents
    docs = get_all_pending_documents()
    return docs

@router.post("/admin/confirm/{id}")
async def confirm_document(id: int, request: Request):
    try:
        get_current_user(request)
    except HTTPException:
        return JSONResponse(status_code=401, content={"success": False, "message": "Not authenticated"})

    from db import get_pending_document_by_id, update_pending_document_status
    pending_doc = get_pending_document_by_id(id)
    if not pending_doc:
        return JSONResponse(status_code=404, content={"success": False, "message": "Pending document not found."})

    if pending_doc["status"] != "PENDING":
        return JSONResponse(status_code=400, content={"success": False, "message": f"Document is already {pending_doc['status']}."})

    doc_type = pending_doc["doc_type"]
    
    # Read manually edited/override payload from body if provided
    try:
        body = await request.json()
        payload = body.get("payload") or pending_doc["payload"]
    except Exception:
        payload = pending_doc["payload"]

    # Validate: reject if any item has null/missing/zero quantity
    items_payload = payload.get("items", [])
    missing_qty_items = []
    for idx, item in enumerate(items_payload):
        qty = item.get("quantity")
        if qty is None or (isinstance(qty, (int, float)) and qty <= 0):
            product_name = item.get("product") or item.get("dish_name") or f"Item #{idx + 1}"
            missing_qty_items.append(product_name)
    if missing_qty_items:
        names = ", ".join(f'"{n}"' for n in missing_qty_items)
        return JSONResponse(
            status_code=400,
            content={
                "success": False,
                "message": f"Cannot confirm: quantity is missing or zero for {names}. Please fill in all quantities before confirming."
            }
        )

    try:
        update_pending_document_status(id, "CONFIRMED")

        if doc_type == "SUPPLIER_INVOICE":
            from ai.invoice.processor import InvoiceProcessor
            proc = InvoiceProcessor()
            res = proc.process(payload)
            if res.get("success"):
                return {
                    "success": True,
                    "message": "Supplier Invoice Confirmed & Logged. Inventory has been updated.",
                    "details": res
                }
            else:
                update_pending_document_status(id, "PENDING")
                return JSONResponse(status_code=400, content={"success": False, "message": f"Failed to process supplier invoice: {res.get('message')}"})

        elif doc_type == "SALES_BILL":
            from db import save_sales_bill, confirm_sales_bill
            from datetime import datetime
            bill_number = payload.get("invoice_number") or f"SB-{id}"
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

            return {
                "success": True,
                "message": f"Sales Bill Confirmed & Logged. Bill Number: {bill_number}. Ingredient consumption calculated.",
                "bill_id": bill_db_id
            }

    except Exception as e:
        update_pending_document_status(id, "PENDING")
        return JSONResponse(status_code=500, content=_server_error(e, f"confirm_document (id={id})"))

@router.post("/admin/reject/{id}")
def reject_document(id: int, request: Request):
    try:
        get_current_user(request)
    except HTTPException:
        return JSONResponse(status_code=401, content={"success": False, "message": "Not authenticated"})

    from db import get_pending_document_by_id, update_pending_document_status
    pending_doc = get_pending_document_by_id(id)
    if not pending_doc:
        return JSONResponse(status_code=404, content={"success": False, "message": "Pending document not found."})

    if pending_doc["status"] != "PENDING":
        return JSONResponse(status_code=400, content={"success": False, "message": f"Document is already {pending_doc['status']}."})

    update_pending_document_status(id, "CANCELLED")
    return {
        "success": True,
        "message": f"Document (ID: {id}) has been discarded/cancelled."
    }

@router.get("/admin/recipes")
def get_recipes(request: Request):
    try:
        get_current_user(request)
    except HTTPException:
        return JSONResponse(status_code=401, content={"success": False, "message": "Not authenticated"})

    from db import get_all_recipes
    recipes = get_all_recipes()
    return recipes

@router.post("/admin/recipes")
async def save_recipe_route(request: Request):
    try:
        get_current_user(request)
    except HTTPException:
        return JSONResponse(status_code=401, content={"success": False, "message": "Not authenticated"})

    try:
        data = await request.json()
    except Exception:
        return JSONResponse(status_code=400, content={"success": False, "message": "Invalid JSON body"})

    dish_name = data.get("dish_name", "").strip()
    ingredients = data.get("ingredients", [])

    if not dish_name:
        return JSONResponse(status_code=400, content={"success": False, "message": "Dish name is required"})
    if not isinstance(ingredients, list) or not ingredients:
        return JSONResponse(status_code=400, content={"success": False, "message": "At least one ingredient is required"})

    valid_ingredients = []
    for idx, ing in enumerate(ingredients):
        ing_name = ing.get("ingredient_name", "").strip()
        qty = ing.get("quantity_per_unit")
        unit = ing.get("unit", "").strip()

        if not ing_name:
            return JSONResponse(status_code=400, content={"success": False, "message": f"Ingredient name is required at row {idx+1}"})
        if qty is None:
            return JSONResponse(status_code=400, content={"success": False, "message": f"Quantity is required for ingredient '{ing_name}'"})
        try:
            qty_val = float(qty)
            if qty_val <= 0:
                raise ValueError()
        except ValueError:
            return JSONResponse(status_code=400, content={"success": False, "message": f"Quantity must be a positive number for ingredient '{ing_name}'"})
        if not unit:
            return JSONResponse(status_code=400, content={"success": False, "message": f"Unit is required for ingredient '{ing_name}'"})

        valid_ingredients.append({
            "ingredient_name": ing_name,
            "quantity_per_unit": qty_val,
            "unit": unit
        })

    from db import save_recipe
    try:
        save_recipe(dish_name, valid_ingredients)
        return {"success": True, "message": f"Recipe for '{dish_name}' saved successfully!"}
    except Exception as e:
        return JSONResponse(status_code=500, content=_server_error(e, "save_recipe_route"))

@router.post("/admin/recipes/delete")
async def delete_recipe_route(request: Request):
    try:
        get_current_user(request)
    except HTTPException:
        return JSONResponse(status_code=401, content={"success": False, "message": "Not authenticated"})

    try:
        data = await request.json()
    except Exception:
        return JSONResponse(status_code=400, content={"success": False, "message": "Invalid JSON body"})

    dish_name = data.get("dish_name", "").strip()
    if not dish_name:
        return JSONResponse(status_code=400, content={"success": False, "message": "Dish name is required for deletion"})

    from db import delete_recipe
    try:
        delete_recipe(dish_name)
        return {"success": True, "message": f"Recipe for '{dish_name}' deleted successfully!"}
    except Exception as e:
        return JSONResponse(status_code=500, content=_server_error(e, "delete_recipe_route"))

# ==================================================
# INVENTORY MANAGEMENT (Dashboard API)
# ==================================================

@router.get("/admin/inventory")
def get_inventory_route(request: Request):
    """Get current inventory items."""
    try:
        get_current_user(request)
    except HTTPException:
        return JSONResponse(status_code=401, content={"success": False, "message": "Not authenticated"})

    from db import get_inventory
    try:
        items = get_inventory()
        inventory = []
        for item in items:
            inventory.append({
                "id": item[0],
                "product_name": item[1],
                "current_stock": item[2],
                "minimum_stock": item[3],
                "unit": item[4],
                "updated_at": item[5]
            })
        return {"success": True, "data": inventory}
    except Exception as e:
        return JSONResponse(status_code=500, content=_server_error(e, "get_inventory_route"))


@router.post("/admin/inventory/add")
async def add_inventory_route(request: Request):
    """Add or create a new inventory item."""
    try:
        get_current_user(request)
    except HTTPException:
        return JSONResponse(status_code=401, content={"success": False, "message": "Not authenticated"})

    try:
        data = await request.json()
    except Exception:
        return JSONResponse(status_code=400, content={"success": False, "message": "Invalid JSON body"})

    product_name = data.get("product_name", "").strip()
    current_stock = data.get("current_stock")
    minimum_stock = data.get("minimum_stock")
    unit = data.get("unit", "").strip()

    if not product_name or not unit:
        return JSONResponse(status_code=400, content={"success": False, "message": "Product name and unit are required"})

    try:
        current_stock = float(current_stock) if current_stock is not None else 0
        minimum_stock = float(minimum_stock) if minimum_stock is not None else 0
    except ValueError:
        return JSONResponse(status_code=400, content={"success": False, "message": "Stock values must be numeric"})

    if current_stock < 0 or minimum_stock < 0:
        return JSONResponse(status_code=400, content={"success": False, "message": "Stock values cannot be negative"})

    from db import save_inventory, log_inventory_audit
    try:
        save_inventory(product_name, current_stock, minimum_stock, unit)
        log_inventory_audit(
            product_name=product_name,
            action_type="SET_ABSOLUTE",
            old_stock=None,
            new_stock=current_stock,
            old_unit=None,
            new_unit=unit,
            old_minimum=None,
            new_minimum=minimum_stock,
            source="dashboard",
            user_phone=None,
            notes=f"Added via dashboard"
        )
        return {"success": True, "message": f"Product '{product_name}' added to inventory"}
    except Exception as e:
        return JSONResponse(status_code=500, content=_server_error(e, "add_inventory_route"))


@router.post("/admin/inventory/update")
async def update_inventory_route(request: Request):
    """Update inventory item (SET absolute or ADJUST delta)."""
    try:
        get_current_user(request)
    except HTTPException:
        return JSONResponse(status_code=401, content={"success": False, "message": "Not authenticated"})

    try:
        data = await request.json()
    except Exception:
        return JSONResponse(status_code=400, content={"success": False, "message": "Invalid JSON body"})

    product_name = data.get("product_name", "").strip()
    new_stock = data.get("new_stock")
    new_minimum = data.get("new_minimum")
    action_type = data.get("action_type", "SET_ABSOLUTE")  # SET_ABSOLUTE or ADJUST_DELTA
    confirm_unit_change = data.get("confirm_unit_change", False)

    if not product_name:
        return JSONResponse(status_code=400, content={"success": False, "message": "Product name is required"})

    from db import get_product_inventory, save_inventory, update_inventory, log_inventory_audit

    try:
        existing = get_product_inventory(product_name)
        if not existing:
            return JSONResponse(status_code=404, content={"success": False, "message": f"Product '{product_name}' not found"})

        old_stock = existing[2]
        old_minimum = existing[3]
        old_unit = existing[4]

        # Handle stock update
        if new_stock is not None:
            try:
                new_stock = float(new_stock)
            except ValueError:
                return JSONResponse(status_code=400, content={"success": False, "message": "Stock value must be numeric"})

            if new_stock < 0:
                return JSONResponse(status_code=400, content={"success": False, "message": "Stock cannot be negative"})

            if action_type == "ADJUST_DELTA":
                final_stock = old_stock + new_stock
                if final_stock < 0:
                    return JSONResponse(status_code=400, content={"success": False, "message": f"Cannot adjust by {new_stock} (would result in negative stock)"})
                update_inventory(product_name, new_stock)
            else:  # SET_ABSOLUTE
                save_inventory(product_name, new_stock, old_minimum, old_unit)
                final_stock = new_stock
        else:
            final_stock = old_stock

        # Update minimum if provided
        if new_minimum is not None:
            try:
                new_minimum = float(new_minimum)
            except ValueError:
                return JSONResponse(status_code=400, content={"success": False, "message": "Minimum value must be numeric"})

            if new_minimum < 0:
                return JSONResponse(status_code=400, content={"success": False, "message": "Minimum cannot be negative"})

            save_inventory(product_name, final_stock, new_minimum, old_unit)
        else:
            new_minimum = old_minimum

        # Log audit
        log_inventory_audit(
            product_name=product_name,
            action_type=action_type,
            old_stock=old_stock,
            new_stock=final_stock if new_stock is not None else old_stock,
            old_unit=old_unit,
            new_unit=old_unit,
            old_minimum=old_minimum,
            new_minimum=new_minimum,
            source="dashboard",
            user_phone=None,
            notes=f"Updated via dashboard"
        )

        return {
            "success": True,
            "message": f"Inventory for '{product_name}' updated successfully"
        }

    except Exception as e:
        return JSONResponse(status_code=500, content=_server_error(e, "update_inventory_route"))


@router.get("/admin/inventory/audit-log")
def get_audit_log_route(request: Request, product_name: str = None, limit: int = 50):
    """Get inventory audit log."""
    try:
        get_current_user(request)
    except HTTPException:
        return JSONResponse(status_code=401, content={"success": False, "message": "Not authenticated"})

    from db import get_inventory_audit_log
    try:
        logs = get_inventory_audit_log(product_name, limit)
        audit = []
        for log in logs:
            audit.append({
                "id": log[0],
                "product_name": log[1],
                "action_type": log[2],
                "old_stock": log[3],
                "new_stock": log[4],
                "old_unit": log[5],
                "new_unit": log[6],
                "old_minimum": log[7],
                "new_minimum": log[8],
                "source": log[9],
                "user_phone": log[10],
                "notes": log[11],
                "created_at": log[12]
            })
        return {"success": True, "data": audit}
    except Exception as e:
        return JSONResponse(status_code=500, content=_server_error(e, "get_audit_log_route"))


@router.post("/admin/inventory/delete")
async def delete_inventory_route(request: Request):
    """Soft-delete an inventory item (mark as inactive)."""
    try:
        get_current_user(request)
    except HTTPException:
        return JSONResponse(status_code=401, content={"success": False, "message": "Not authenticated"})

    try:
        data = await request.json()
    except Exception:
        return JSONResponse(status_code=400, content={"success": False, "message": "Invalid JSON body"})

    product_name = data.get("product_name", "").strip()
    if not product_name:
        return JSONResponse(status_code=400, content={"success": False, "message": "Product name is required"})

    from db import get_product_inventory, delete_inventory

    try:
        existing = get_product_inventory(product_name)
        if not existing:
            return JSONResponse(status_code=404, content={"success": False, "message": f"Product '{product_name}' not found"})

        delete_inventory(product_name)
        return {"success": True, "message": f"Product '{product_name}' has been deactivated"}

    except Exception as e:
        return JSONResponse(status_code=500, content=_server_error(e, "delete_inventory_route"))

@router.get("/admin/inventory-deductions/pending")
def get_pending_deductions_route(request: Request):
    try:
        get_current_user(request)
    except HTTPException:
        return JSONResponse(status_code=401, content={"success": False, "message": "Not authenticated"})

    from db import get_pending_inventory_deductions
    try:
        deductions = get_pending_inventory_deductions()
        return deductions
    except Exception as e:
        return JSONResponse(status_code=500, content=_server_error(e, "get_pending_deductions_route"))

@router.post("/admin/inventory-deductions/{id}/approve")
def approve_deduction_route(id: int, request: Request):
    try:
        get_current_user(request)
    except HTTPException:
        return JSONResponse(status_code=401, content={"success": False, "message": "Not authenticated"})

    from db import approve_inventory_deduction
    try:
        success, message = approve_inventory_deduction(id)
        if success:
            return {"success": True, "message": message}
        else:
            return JSONResponse(status_code=400, content={"success": False, "message": message})
    except Exception as e:
        return JSONResponse(status_code=500, content=_server_error(e, f"approve_deduction_route (id={id})"))

@router.post("/admin/inventory-deductions/{id}/reject")
def reject_deduction_route(id: int, request: Request):
    try:
        get_current_user(request)
    except HTTPException:
        return JSONResponse(status_code=401, content={"success": False, "message": "Not authenticated"})

    from db import reject_inventory_deduction
    try:
        success, message = reject_inventory_deduction(id)
        if success:
            return {"success": True, "message": message}
        else:
            return JSONResponse(status_code=400, content={"success": False, "message": message})
    except Exception as e:
        return JSONResponse(status_code=500, content=_server_error(e, f"reject_deduction_route (id={id})"))