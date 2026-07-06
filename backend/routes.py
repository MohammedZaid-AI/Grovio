from fastapi import APIRouter, Form, Request, HTTPException, UploadFile, File
from fastapi.responses import Response, HTMLResponse, FileResponse, RedirectResponse, JSONResponse
from twilio.twiml.messaging_response import MessagingResponse
from twilio.request_validator import RequestValidator
import os
import jwt
import datetime
import uuid

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
    auth_token = os.getenv("TWILIO_AUTH_TOKEN")
    if auth_token:
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
    return FileResponse("backend/static/login.html")

@router.get("/admin")
def get_dashboard(request: Request):
    if not check_authenticated(request):
        return RedirectResponse(url="/admin/login", status_code=303)
    return FileResponse("backend/static/dashboard.html")

@router.post("/admin/login")
async def login(request: Request, password: str = Form(...)):
    expected_password = os.getenv("DASHBOARD_PASSWORD")
    if not expected_password:
        return JSONResponse(status_code=401, content={"success": False, "message": "Dashboard password is not configured on server."})
    
    if password == expected_password:
        token = create_access_token({"sub": "admin"})
        response = JSONResponse(content={"success": True})
        response.set_cookie(
            key="session_token",
            value=token,
            httponly=True,
            samesite="lax",
            max_age=24*3600
        )
        return response
    
    return JSONResponse(status_code=401, content={"success": False, "message": "Incorrect password"})

@router.post("/admin/logout")
def logout():
    response = JSONResponse(content={"success": True})
    response.delete_cookie("session_token")
    return response

async def process_web_upload(file: UploadFile, doc_type: str):
    # 1. Type validation
    allowed_types = ["application/pdf", "image/jpeg", "image/jpg", "image/png"]
    if file.content_type not in allowed_types:
        raise HTTPException(status_code=400, detail="Invalid file type. Only PDF and JPEG/PNG images are allowed.")

    # 2. Size validation (max 10MB)
    max_size = 10 * 1024 * 1024
    contents = await file.read()
    if len(contents) > max_size:
        raise HTTPException(status_code=400, detail="File too large. Maximum size is 10MB.")

    # Reset cursor after reading
    await file.seek(0)

    # 3. Create temp file path
    os.makedirs("downloads", exist_ok=True)
    temp_filename = f"web_{uuid.uuid4().hex}_{file.filename}"
    temp_filepath = os.path.join("downloads", temp_filename)

    try:
        # Save temp file
        with open(temp_filepath, "wb") as f:
            f.write(contents)

        # 4. Parse local file
        parsed = pipeline.parser.parse_local(temp_filepath, file.content_type)
        
        # Save raw OCR text for debug
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
        return JSONResponse(status_code=500, content={"success": False, "message": str(e)})

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
        return JSONResponse(status_code=500, content={"success": False, "message": str(e)})

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
        return JSONResponse(status_code=500, content={"success": False, "message": f"Unexpected error during processing: {str(e)}"})

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