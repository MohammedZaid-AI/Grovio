"""Bulk invoice upload: failure isolation + tally + bulk approve reuse.
Mocks the extraction/commit boundary; exercises the real batch orchestration."""
import asyncio, os, sys, tempfile
from unittest.mock import patch

sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("JWT_SECRET", "x" * 48)

import db
_tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False); _tmp.close()
db.DB_PATH = _tmp.name; db.init_db()

import backend.routes as routes

_ok = _fail = 0
def check(name, cond):
    global _ok, _fail
    if cond: _ok += 1; print(f"  ✅ {name}")
    else: _fail += 1; print(f"  ❌ {name}")

INVOICE = {"supplier": "Acme", "invoice_number": "INV-1", "total_amount": 100,
           "items": [{"product": "Rice", "quantity": 2, "unit": "kg", "unit_price": 50, "total": 100}]}

def fake_extract(filepath, content_type, doc_type):
    # A file whose stored path contains "bad" fails extraction; others succeed.
    if "bad" in os.path.basename(filepath):
        raise ValueError("LLM Extraction failed: No line items could be parsed.")
    inv = dict(INVOICE, doc_type=doc_type)
    doc_id = db.save_pending_document(f"web_{os.path.basename(filepath)}", doc_type, inv)
    return doc_id, inv

def seed_batch():
    batch = "testbatch"
    d = tempfile.mkdtemp()
    for name in ("good1.pdf", "good2.pdf", "bad.pdf"):
        p = os.path.join(d, name)
        open(p, "wb").write(b"x")
        db.create_batch_file(batch, name, "SUPPLIER_INVOICE", p, status="PENDING")
    return batch

with patch.object(routes, "extract_to_pending", fake_extract):
    batch = seed_batch()
    asyncio.run(routes._process_batch(batch))

files = {f["filename"]: f for f in db.get_batch_files(batch)}
print("\n[1] Extraction with one bad file")
check("good1 extracted", files["good1.pdf"]["status"] == "EXTRACTED")
check("good2 extracted", files["good2.pdf"]["status"] == "EXTRACTED")
check("bad failed (others unaffected)", files["bad.pdf"]["status"] == "FAILED")
check("failed row carries error", bool(files["bad.pdf"]["error"]))
check("extracted rows link a pending doc", files["good1.pdf"]["pending_doc_id"] is not None)

view = routes._batch_view(batch)
print("\n[2] Batch view tally")
check("2 extracted / 1 failed", view["counts"]["EXTRACTED"] == 2 and view["counts"]["FAILED"] == 1)
check("processing marked done", view["processing_done"] is True)
check("review row exposes supplier/invoice/total",
      files["good1.pdf"]["supplier"] == "Acme" and files["good1.pdf"]["total_amount"] == 100)

print("\n[3] Bulk approve reuses commit_pending_document")
with patch("ai.invoice.processor.InvoiceProcessor.process", lambda self, p: {"success": True}):
    res = routes.commit_pending_document  # ensure symbol exists
    # call the endpoint's core via the same helper the route uses
    approved = 0
    for f in db.get_batch_files(batch):
        if f["status"] == "EXTRACTED" and f["doc_status"] == "PENDING":
            code, _ = routes.commit_pending_document(f["pending_doc_id"])
            approved += 1 if code == 200 else 0
check("both extracted docs approved", approved == 2)
after = {f["filename"]: f for f in db.get_batch_files(batch)}
check("approved docs now CONFIRMED", after["good1.pdf"]["doc_status"] == "CONFIRMED")

print("\n" + "=" * 60)
print(f"RESULT: {_ok} passed, {_fail} failed")
try: os.unlink(_tmp.name)
except OSError: pass
sys.exit(1 if _fail else 0)
