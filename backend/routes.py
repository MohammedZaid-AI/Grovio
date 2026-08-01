from fastapi import APIRouter, Form, Request, HTTPException, UploadFile, File
from fastapi.responses import Response, HTMLResponse, FileResponse, RedirectResponse, JSONResponse
from twilio.twiml.messaging_response import MessagingResponse
from twilio.request_validator import RequestValidator
from typing import List
import os
import jwt
import datetime
import uuid
import hmac
import asyncio
import collections

from core.logger import logger

# Startup check: fail if JWT_SECRET is not in environment or is too weak
JWT_SECRET = os.getenv("JWT_SECRET")
if not JWT_SECRET:
    raise RuntimeError("JWT_SECRET environment variable is NOT set. The server cannot start.")
if len(JWT_SECRET.encode("utf-8")) < 32:
    raise RuntimeError("JWT_SECRET must be at least 32 bytes long to ensure secure HMAC-SHA256 signing.")

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

    From: str = Form(""),

    MessageSid: str = Form("")

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
        # The reconstructed url must EXACTLY match the Twilio console URL (scheme +
        # host + path, no trailing slash). http-vs-https behind a tunnel is the usual
        # cause. No secrets logged.
        print(f"🚫 Webhook signature verification failed (reconstructed url: {url})")
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
    # Async delivery (Phase 2): persist + return 200 immediately
    # -------------------------------------------------------
    # We do NOT run the ConversationEngine here. LLM 429 retries + LangGraph +
    # MCP can exceed Twilio's ~15s webhook timeout, which makes Twilio discard
    # the TwiML reply (lost messages). Instead we persist the message, return an
    # empty 200 in milliseconds, and let the background worker process it and
    # deliver the reply via the Twilio REST API (no webhook timeout).
    from backend.whatsapp_worker import enqueue_and_wake

    try:
        inbound_id, is_new = await enqueue_and_wake(
            message_sid=MessageSid,
            phone=From,
            body=Body,
            num_media=NumMedia,
        )
        if not is_new:
            print(f"↩️  Duplicate webhook ignored (MessageSid={MessageSid}, inbound_id={inbound_id})")
        else:
            print(f"✅ Queued inbound_id={inbound_id} for async delivery")
    except Exception as e:
        # Never fail the webhook: log and still return 200 so Twilio doesn't
        # spin on retries. Loss here is limited to the enqueue itself, which is
        # logged (not silent).
        logger.error(f"webhook enqueue failed: {e}", exc_info=True)

    # Empty reply: Twilio sends nothing from the webhook now — the worker
    # delivers via the REST API. whatsapp_reply([]) yields an empty <Response>
    # (and keeps the DEBUG-gated final-output logging in one place).
    return whatsapp_reply([])


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


# Reverse of _UPLOAD_EXTENSIONS — used when re-deriving content_type from a
# stored batch file (whose extension we control).
_EXT_CONTENT_TYPE = {".pdf": "application/pdf", ".jpg": "image/jpeg", ".png": "image/png"}


def extract_to_pending(filepath: str, content_type: str, doc_type: str):
    """Reusable single-invoice extraction core: OCR + LLM -> pending_document.

    Returns (doc_id, invoice). Raises ValueError on bad type / extraction failure.
    Does NOT own the file lifecycle — the caller creates and removes `filepath`.
    Both the single-upload endpoint and the bulk worker go through here, so OCR
    and parsing logic exists in exactly one place.
    """
    if content_type not in _UPLOAD_EXTENSIONS:
        raise ValueError("Invalid file type. Only PDF and JPEG/PNG images are allowed.")

    parsed = pipeline.parser.parse_local(filepath, content_type)

    # Save raw OCR text for debug (dev-only: can contain customer/financial data).
    if _debug_enabled():
        debug_dir = os.path.join("downloads", "ocr_debug")
        os.makedirs(debug_dir, exist_ok=True)
        debug_filepath = os.path.join(debug_dir, f"raw_ocr_{doc_type}_{uuid.uuid4().hex}.txt")
        with open(debug_filepath, "w", encoding="utf-8") as f_debug:
            f_debug.write(parsed.get("text", ""))
        print(f"[DEBUG] Raw OCR text saved to: {debug_filepath}")

    invoice = pipeline.extractor.extract(parsed["text"])
    if invoice.get("error"):
        raise ValueError(f"LLM Extraction failed: {invoice['error']}")
    if not invoice.get("items"):
        raise ValueError("LLM Extraction failed: No line items could be parsed. Please verify receipt image quality and try again.")

    invoice["doc_type"] = doc_type
    from db import save_pending_document
    doc_id = save_pending_document(phone=f"web_{uuid.uuid4().hex}", doc_type=doc_type, payload=invoice)
    return doc_id, invoice


async def process_web_upload(file: UploadFile, doc_type: str):
    # 1. Type validation
    if file.content_type not in _UPLOAD_EXTENSIONS:
        raise HTTPException(status_code=400, detail="Invalid file type. Only PDF and JPEG/PNG images are allowed.")

    # 2. Size validation (max 10MB)
    max_size = 10 * 1024 * 1024
    contents = await file.read()
    if len(contents) > max_size:
        raise HTTPException(status_code=400, detail="File too large. Maximum size is 10MB.")

    # SECURITY (M-3): temp filename/extension derived from the whitelisted
    # content_type, never the client filename (path-traversal safe).
    os.makedirs("downloads", exist_ok=True)
    ext = _UPLOAD_EXTENSIONS[file.content_type]
    temp_filepath = os.path.join("downloads", f"web_{uuid.uuid4().hex}{ext}")

    try:
        with open(temp_filepath, "wb") as f:
            f.write(contents)
        try:
            doc_id, invoice = extract_to_pending(temp_filepath, file.content_type, doc_type)
        except ValueError as ve:
            raise HTTPException(status_code=400, detail=str(ve))
        return {"success": True, "id": doc_id, "invoice": invoice}
    finally:
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

def commit_pending_document(doc_id: int, payload: dict = None):
    """Commit ONE pending document to inventory / sales. Shared by the single
    confirm route and bulk approve. Returns (status_code, response_dict)."""
    from db import get_pending_document_by_id, update_pending_document_status
    pending_doc = get_pending_document_by_id(doc_id)
    if not pending_doc:
        return 404, {"success": False, "message": "Pending document not found."}
    if pending_doc["status"] != "PENDING":
        return 400, {"success": False, "message": f"Document is already {pending_doc['status']}."}

    doc_type = pending_doc["doc_type"]
    if payload is None:
        payload = pending_doc["payload"]

    # Reject if any item has null/missing/zero quantity.
    missing_qty_items = []
    for idx, item in enumerate(payload.get("items", [])):
        qty = item.get("quantity")
        if qty is None or (isinstance(qty, (int, float)) and qty <= 0):
            missing_qty_items.append(item.get("product") or item.get("dish_name") or f"Item #{idx + 1}")
    if missing_qty_items:
        names = ", ".join(f'"{n}"' for n in missing_qty_items)
        return 400, {"success": False,
                     "message": f"Cannot confirm: quantity is missing or zero for {names}. Please fill in all quantities before confirming."}

    try:
        update_pending_document_status(doc_id, "CONFIRMED")

        if doc_type == "SUPPLIER_INVOICE":
            from ai.invoice.processor import InvoiceProcessor
            res = InvoiceProcessor().process(payload)
            if res.get("success"):
                return 200, {"success": True,
                             "message": "Supplier Invoice Confirmed & Logged. Inventory has been updated.",
                             "details": res}
            update_pending_document_status(doc_id, "PENDING")
            return 400, {"success": False, "message": f"Failed to process supplier invoice: {res.get('message')}"}

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
                    items.append({"dish_name": item.get("product"), "quantity": int(qty),
                                  "unit_price": item.get("unit_price"), "total_price": item.get("total")})
            import sqlite3
            try:
                bill_db_id = save_sales_bill(bill_number, bill_date, total_amount, items, status='PENDING_CONFIRMATION')
            except sqlite3.IntegrityError:
                update_pending_document_status(doc_id, "PENDING")
                return 409, {"success": False,
                             "message": f"Bill number \"{bill_number}\" has already been logged. "
                                        f"Edit the bill number to a unique value before confirming."}
            confirm_sales_bill(bill_db_id)
            return 200, {"success": True,
                         "message": f"Sales Bill Confirmed & Logged. Bill Number: {bill_number}. Ingredient consumption calculated.",
                         "bill_id": bill_db_id}

        return 400, {"success": False, "message": f"Unknown document type: {doc_type}"}
    except Exception as e:
        update_pending_document_status(doc_id, "PENDING")
        return 500, _server_error(e, f"commit_pending_document (id={doc_id})")


@router.post("/admin/confirm/{id}")
async def confirm_document(id: int, request: Request):
    try:
        get_current_user(request)
    except HTTPException:
        return JSONResponse(status_code=401, content={"success": False, "message": "Not authenticated"})

    # Read manually edited/override payload from the body if provided.
    try:
        body = await request.json()
        payload = body.get("payload")
    except Exception:
        payload = None

    code, content = commit_pending_document(id, payload)
    return JSONResponse(status_code=code, content=content) if code != 200 else content

# ===========================================================================
# BULK INVOICE UPLOAD
# ===========================================================================
# Configurable concurrency (default 3). Each file reuses extract_to_pending;
# extracted files become normal pending_documents that flow through the
# existing single-confirm UI for individual corrections.
_BULK_WORKERS = max(1, int(os.getenv("BULK_UPLOAD_WORKERS", "3")))
_BATCH_MAX_FILES = 25  # ponytail: guardrail; raise if real batches are larger


async def _process_batch_file(file_id: int):
    """Extract one batch file. Never raises — failures are recorded on the row
    so the rest of the batch continues."""
    from db import get_batch_file, set_batch_file_status
    f = get_batch_file(file_id)
    if not f or not f.get("filepath"):
        return
    set_batch_file_status(file_id, "PROCESSING", bump_attempt=True)
    content_type = _EXT_CONTENT_TYPE.get(os.path.splitext(f["filepath"])[1].lower())
    try:
        # OCR + LLM is blocking; run off the event loop so workers run in parallel.
        doc_id, _ = await asyncio.to_thread(extract_to_pending, f["filepath"], content_type, f["doc_type"])
        set_batch_file_status(file_id, "EXTRACTED", pending_doc_id=doc_id)
        try:
            os.remove(f["filepath"])  # keep only failed files, for retry
        except OSError:
            pass
    except Exception as e:
        logger.error(f"batch file {file_id} extraction failed: {e}", exc_info=True)
        set_batch_file_status(file_id, "FAILED", error=str(e))


async def _process_batch(batch_id: str):
    from db import get_batch_files
    sem = asyncio.Semaphore(_BULK_WORKERS)

    async def run(fid):
        async with sem:
            await _process_batch_file(fid)

    pending = [f["id"] for f in get_batch_files(batch_id) if f["status"] in ("PENDING", "FAILED")]
    await asyncio.gather(*(run(fid) for fid in pending))


@router.post("/admin/upload/batch")
async def upload_batch(request: Request, files: List[UploadFile] = File(...), doc_type: str = Form(...)):
    try:
        get_current_user(request)
    except HTTPException:
        return JSONResponse(status_code=401, content={"success": False, "message": "Not authenticated"})

    if doc_type not in ("SUPPLIER_INVOICE", "SALES_BILL"):
        return JSONResponse(status_code=400, content={"success": False, "message": "Invalid doc_type."})
    if not files:
        return JSONResponse(status_code=400, content={"success": False, "message": "No files uploaded."})
    if len(files) > _BATCH_MAX_FILES:
        return JSONResponse(status_code=400, content={"success": False, "message": f"Too many files. Max {_BATCH_MAX_FILES} per batch."})

    from db import create_batch_file
    batch_id = uuid.uuid4().hex
    batch_dir = os.path.join("downloads", "batches", batch_id)
    os.makedirs(batch_dir, exist_ok=True)
    max_size = 10 * 1024 * 1024

    for file in files:
        name = file.filename or "file"
        # Bad type / oversize become FAILED rows so they still show in the review.
        if file.content_type not in _UPLOAD_EXTENSIONS:
            create_batch_file(batch_id, name, doc_type, None, status="FAILED",
                              error="Unsupported file type (PDF/JPG/PNG only).")
            continue
        contents = await file.read()
        if len(contents) > max_size:
            create_batch_file(batch_id, name, doc_type, None, status="FAILED", error="File exceeds 10MB.")
            continue
        ext = _UPLOAD_EXTENSIONS[file.content_type]  # M-3: extension from whitelisted type
        path = os.path.join(batch_dir, f"{uuid.uuid4().hex}{ext}")
        with open(path, "wb") as fh:
            fh.write(contents)
        create_batch_file(batch_id, name, doc_type, path, status="PENDING")

    # Fire-and-forget: the UI polls /admin/batch/{id} for progress.
    asyncio.create_task(_process_batch(batch_id))
    return {"success": True, "batch_id": batch_id, "total": len(files)}


def _batch_view(batch_id: str):
    from db import get_batch_files
    files = get_batch_files(batch_id)
    counts = {"total": len(files), "PENDING": 0, "PROCESSING": 0, "EXTRACTED": 0, "FAILED": 0, "confirmed": 0}
    for f in files:
        counts[f["status"]] = counts.get(f["status"], 0) + 1
        if f.get("doc_status") == "CONFIRMED":
            counts["confirmed"] += 1
    done = all(f["status"] in ("EXTRACTED", "FAILED") for f in files) if files else True
    return {"batch_id": batch_id, "files": files, "counts": counts, "processing_done": done}


@router.get("/admin/batch/{batch_id}")
def get_batch(batch_id: str, request: Request):
    try:
        get_current_user(request)
    except HTTPException:
        return JSONResponse(status_code=401, content={"success": False, "message": "Not authenticated"})
    return _batch_view(batch_id)


@router.post("/admin/batch/{batch_id}/approve")
def approve_batch(batch_id: str, request: Request):
    """Bulk-commit every extracted-and-still-pending document in the batch.
    Individual corrections stay per-doc via /admin/confirm/{pending_doc_id}."""
    try:
        get_current_user(request)
    except HTTPException:
        return JSONResponse(status_code=401, content={"success": False, "message": "Not authenticated"})

    from db import get_batch_files
    results = []
    for f in get_batch_files(batch_id):
        if f["status"] == "EXTRACTED" and f["doc_status"] == "PENDING":
            code, res = commit_pending_document(f["pending_doc_id"])
            results.append({"file_id": f["id"], "pending_doc_id": f["pending_doc_id"],
                            "ok": code == 200, "message": res.get("message")})
    approved = sum(1 for r in results if r["ok"])
    return {"success": True, "approved": approved, "attempted": len(results), "results": results}


@router.post("/admin/batch/{batch_id}/retry/{file_id}")
async def retry_batch_file(batch_id: str, file_id: int, request: Request):
    try:
        get_current_user(request)
    except HTTPException:
        return JSONResponse(status_code=401, content={"success": False, "message": "Not authenticated"})

    from db import get_batch_file
    f = get_batch_file(file_id)
    if not f or f["batch_id"] != batch_id:
        return JSONResponse(status_code=404, content={"success": False, "message": "File not found in batch."})
    if f["status"] != "FAILED":
        return JSONResponse(status_code=400, content={"success": False, "message": f"Only FAILED files can be retried (this is {f['status']})."})
    if not f.get("filepath") or not os.path.exists(f["filepath"]):
        return JSONResponse(status_code=400, content={"success": False, "message": "Original file is no longer available to retry."})

    asyncio.create_task(_process_batch_file(file_id))
    return {"success": True, "message": "Retry started."}


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

    from db import get_product_inventory, save_inventory, log_inventory_audit
    try:
        existing = get_product_inventory(product_name)
        if existing:
            # Adding to a product that already exists ACCUMULATES stock
            # (add-to-existing), keeping its canonical name; minimum/unit refresh.
            canonical = existing[1]
            old_stock = existing[2] or 0
            new_total = old_stock + current_stock
            save_inventory(canonical, new_total, minimum_stock, unit)
            log_inventory_audit(
                product_name=canonical,
                action_type="ADD",
                old_stock=old_stock,
                new_stock=new_total,
                old_unit=existing[4],
                new_unit=unit,
                old_minimum=existing[3],
                new_minimum=minimum_stock,
                source="dashboard",
                user_phone=None,
                notes="Added to existing stock via dashboard",
            )
            return {"success": True,
                    "message": f"Added {current_stock} {unit} to '{canonical}' — now {new_total} {unit}"}

        # New product
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
            notes="Added via dashboard",
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

        # Handle stock update. The two modes take DIFFERENT inputs:
        #   ADJUST_DELTA -> a signed delta (dashboard sends `delta_quantity`);
        #                   validate the RESULT, not the delta, so removing stock works.
        #   SET_ABSOLUTE -> an absolute `new_stock` that must be >= 0.
        if action_type == "ADJUST_DELTA":
            # Back-compat: the delta was historically sent as `new_stock`.
            delta = data.get("delta_quantity")
            if delta is None:
                delta = new_stock
            if delta is not None:
                try:
                    delta = float(delta)
                except (ValueError, TypeError):
                    return JSONResponse(status_code=400, content={"success": False, "message": "Adjustment value must be numeric"})
                final_stock = old_stock + delta
                if final_stock < 0:
                    return JSONResponse(status_code=400, content={"success": False, "message": f"Cannot adjust by {delta} (would result in negative stock)"})
                update_inventory(product_name, delta)
            else:
                final_stock = old_stock
        else:  # SET_ABSOLUTE
            if new_stock is not None:
                try:
                    new_stock = float(new_stock)
                except (ValueError, TypeError):
                    return JSONResponse(status_code=400, content={"success": False, "message": "Stock value must be numeric"})
                if new_stock < 0:
                    return JSONResponse(status_code=400, content={"success": False, "message": "Stock cannot be negative"})
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