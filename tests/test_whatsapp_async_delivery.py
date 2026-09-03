"""
Phase 2 reliability tests: async WhatsApp delivery.

Covers the delivery layer that removes the Twilio webhook-timeout failure mode:
  * duplicate webhook protection (dedup by MessageSid)
  * background processing via the concierge (mocked at worker.respond)
  * ordered multi-part delivery / long-response chunking
  * send retries + hard-failure recording (never silently dropped)
  * restart recovery (re-send pending outbound; fail interrupted inbound)
  * multiple concurrent conversations (per-phone isolation)
  * no duplicate replies (SENT parts are not re-sent)

Network is never touched: concierge.respond and send_whatsapp are mocked.
Run:  python tests/test_whatsapp_async_delivery.py
"""
import asyncio
import os
import sys
import tempfile
from unittest.mock import AsyncMock, Mock, patch

sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import httpx

# The messaging layer itself is covered by tests/test_gateway_transport.py; this suite
# proves the DELIVERY pipeline — ordering, dedup, retries, restart recovery —
# is unchanged by the move to the Baileys gateway.

# Isolated temp DB BEFORE importing anything that touches db.
import db
_tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
_tmp.close()
db.DB_PATH = _tmp.name
db.init_db()

import backend.whatsapp_worker as worker
from whatsapp import classify_send_error
from whatsapp.gateway import NotConfigured


def gateway_error(status, detail=None):
    """A gateway failure, as httpx would raise it."""
    payload = {"error": detail} if detail else {}
    request = httpx.Request("POST", "http://localhost:8100/send")
    return httpx.HTTPStatusError(
        "boom", request=request,
        response=httpx.Response(status, json=payload, request=request))

# Make retry backoff instant for tests.
worker.SEND_BACKOFF_BASE_SECONDS = 0

_passed = 0
_failed = 0


def check(name, condition):
    global _passed, _failed
    if condition:
        _passed += 1
        print(f"  ✅ {name}")
    else:
        _failed += 1
        print(f"  ❌ {name}")


async def drain(phone):
    """Wait until the phone's worker has fully drained and exited."""
    for _ in range(1000):
        task = worker._workers.get(phone)
        if task:
            await asyncio.gather(task, return_exceptions=True)
        if not db.has_pending_work(phone) and worker._workers.get(phone) is None:
            return
        await asyncio.sleep(0.005)
    raise AssertionError(f"worker for {phone} did not drain")


def outbound_rows(phone):
    conn = db.get_connection()
    try:
        cur = conn.cursor()
        cur.execute(
            "SELECT part_index, body, status, attempts, provider_sid, error_code FROM whatsapp_outbound "
            "WHERE phone = ? ORDER BY inbound_id, part_index",
            (phone,),
        )
        return cur.fetchall()
    finally:
        conn.close()


def inbound_status(phone):
    conn = db.get_connection()
    try:
        cur = conn.cursor()
        cur.execute("SELECT status FROM whatsapp_inbound WHERE phone = ? ORDER BY id", (phone,))
        return [r[0] for r in cur.fetchall()]
    finally:
        conn.close()


def purge(phone):
    """Test-only: drop a phone's queued rows so it can't be picked up by a later
    recovery test."""
    conn = db.get_connection()
    try:
        cur = conn.cursor()
        cur.execute("DELETE FROM whatsapp_outbound WHERE phone = ?", (phone,))
        cur.execute("DELETE FROM whatsapp_inbound WHERE phone = ?", (phone,))
        conn.commit()
    finally:
        conn.close()


# ----------------------------------------------------------------------
async def test_dedup():
    print("\n[1] Duplicate webhook protection (dedup by MessageSid)")
    phone = "whatsapp:+910000000001"
    id1, new1 = db.enqueue_inbound_message("SID-DUP", phone, "hi", 0)
    id2, new2 = db.enqueue_inbound_message("SID-DUP", phone, "hi", 0)
    check("first enqueue is new", new1 is True)
    check("second enqueue is duplicate", new2 is False)
    check("same inbound id returned", id1 == id2)
    check("only one row exists", len(inbound_status(phone)) == 1)
    purge(phone)  # never drained; keep it out of the recovery test


async def test_basic_delivery():
    print("\n[2] Basic processing via concierge + single ordered send")
    phone = "whatsapp:+910000000002"
    sender = AsyncMock(return_value="TWSID-1")
    with patch.object(worker, "respond", new=AsyncMock(return_value="Hello there")), \
         patch.object(worker, "send_text", sender):
        await worker.enqueue_and_wake("SID-2", phone, "hey", 0)
        await drain(phone)
    rows = outbound_rows(phone)
    check("concierge consulted (single reply part)", len(rows) == 1)
    check("reply body delivered", rows and rows[0][1] == "Hello there")
    check("marked SENT with provider sid", rows and rows[0][2] == "SENT" and rows[0][4] == "TWSID-1")
    check("inbound marked DONE", inbound_status(phone) == ["DONE"])
    check("send called exactly once", sender.call_count == 1)


async def test_long_response_ordered():
    print("\n[3] Long response chunked into ordered parts")
    phone = "whatsapp:+910000000003"
    long_reply = "\n".join(f"line-{i}" for i in range(400))  # > 1500 chars, splittable
    order = []
    sender = AsyncMock(side_effect=lambda to, body: (order.append(body), "S")[1])
    with patch.object(worker, "respond", new=AsyncMock(return_value=long_reply)), \
         patch.object(worker, "send_text", sender):
        await worker.enqueue_and_wake("SID-3", phone, "report", 0)
        await drain(phone)
    rows = outbound_rows(phone)
    check("split into multiple parts", len(rows) > 1)
    check("every part within 1500 chars", all(len(r[1]) <= 1500 for r in rows))
    check("part_index is contiguous & ordered", [r[0] for r in rows] == list(range(len(rows))))
    check("all parts SENT", all(r[2] == "SENT" for r in rows))
    check("sent in stored order", order == [r[1] for r in rows])


async def test_send_retry():
    print("\n[4] Send retries then succeeds")
    phone = "whatsapp:+910000000004"
    sender = AsyncMock(side_effect=[Exception("429"), Exception("timeout"), "TWSID-OK"])
    with patch.object(worker, "respond", new=AsyncMock(return_value="retry me")), \
         patch.object(worker, "send_text", sender):
        await worker.enqueue_and_wake("SID-4", phone, "x", 0)
        await drain(phone)
    rows = outbound_rows(phone)
    check("eventually SENT", rows and rows[0][2] == "SENT")
    check("attempts recorded (>=3)", rows and rows[0][3] >= 3)
    check("send retried 3 times", sender.call_count == 3)


async def test_hard_failure_recorded():
    print("\n[5] Hard send failure is recorded, not silently dropped")
    phone = "whatsapp:+910000000005"
    sender = AsyncMock(side_effect=Exception("meta unreachable"))
    with patch.object(worker, "respond", new=AsyncMock(return_value="never sends")), \
         patch.object(worker, "send_text", sender):
        await worker.enqueue_and_wake("SID-5", phone, "x", 0)
        await drain(phone)
    rows = outbound_rows(phone)
    check("row still present (not lost)", len(rows) == 1)
    check("marked FAILED", rows and rows[0][2] == "FAILED")
    check("exhausted max attempts", rows and rows[0][3] == worker.MAX_SEND_ATTEMPTS)


async def test_processing_error_still_replies():
    print("\n[6] Engine exception -> error reply queued + inbound FAILED")
    phone = "whatsapp:+910000000006"
    sender = AsyncMock(return_value="S")
    with patch.object(worker, "respond", new=AsyncMock(side_effect=RuntimeError("boom"))), \
         patch.object(worker, "send_text", sender):
        await worker.enqueue_and_wake("SID-6", phone, "x", 0)
        await drain(phone)
    rows = outbound_rows(phone)
    check("inbound marked FAILED", inbound_status(phone) == ["FAILED"])
    # Assert against the constant, not its wording — user-facing copy is tuned
    # for tone and must not be pinned by a test.
    check("error reply queued and sent",
          rows and rows[0][2] == "SENT" and rows[0][1] == worker._ERROR_REPLY)
    check("apology names neither the product nor the fault",
          not any(w in worker._ERROR_REPLY.lower() for w in ("grovio", "exception", "error code")))


async def test_restart_recovery():
    print("\n[7] Restart recovery: resend pending outbound + fail interrupted inbound")
    phone_out = "whatsapp:+910000000007"
    phone_proc = "whatsapp:+910000000008"

    # (a) A reply that was computed but never sent (crash before/at send).
    iid, _ = db.enqueue_inbound_message("SID-7a", phone_out, "x", 0)
    db.save_reply_and_finish(iid, phone_out, ["recovered reply"])
    # (b) A message interrupted mid-turn: claimed (PROCESSING) but never finished.
    db.enqueue_inbound_message("SID-7b", phone_proc, "y", 0)
    db.claim_next_inbound(phone_proc)  # -> PROCESSING

    sender = AsyncMock(return_value="TWSID-R")
    # Patch the concierge defensively: recovery should not invoke it for these
    # phones (outbound-only / interrupted), but we never want a test to hit a
    # real LLM if that invariant ever regresses.
    with patch.object(worker, "respond", new=AsyncMock(return_value="unexpected")), \
         patch.object(worker, "send_text", sender):
        await worker.recover_pending()
        await drain(phone_out)
        await drain(phone_proc)

    check("interrupted inbound marked FAILED (not reprocessed)", inbound_status(phone_proc) == ["FAILED"])
    out = outbound_rows(phone_out)
    check("pending outbound re-sent on recovery", out and out[0][2] == "SENT")
    check("recovery delivered the exact recovered reply", out and out[0][1] == "recovered reply" and out[0][4] == "TWSID-R")


async def test_concurrent_phones():
    print("\n[8] Multiple concurrent conversations stay isolated")
    pa = "whatsapp:+910000000009"
    pb = "whatsapp:+910000000010"

    async def fake_process(phone, message):
        await asyncio.sleep(0.01)
        return f"reply-for-{phone[-2:]}"

    sender = AsyncMock(return_value="S")
    with patch.object(worker, "respond", new=AsyncMock(side_effect=fake_process)), \
         patch.object(worker, "send_text", sender):
        await worker.enqueue_and_wake("SID-9A", pa, "a", 0)
        await worker.enqueue_and_wake("SID-9B", pb, "b", 0)
        await drain(pa)
        await drain(pb)

    ra = outbound_rows(pa)
    rb = outbound_rows(pb)
    check("phone A got its own reply", ra and ra[0][1] == f"reply-for-{pa[-2:]}")
    check("phone B got its own reply", rb and rb[0][1] == f"reply-for-{pb[-2:]}")
    check("both delivered", ra[0][2] == "SENT" and rb[0][2] == "SENT")


async def test_no_duplicate_resend():
    print("\n[9] Already-SENT parts are never re-sent")
    phone = "whatsapp:+910000000011"
    sender = AsyncMock(return_value="S")
    with patch.object(worker, "respond", new=AsyncMock(return_value="once only")), \
         patch.object(worker, "send_text", sender):
        await worker.enqueue_and_wake("SID-11", phone, "x", 0)
        await drain(phone)
        # Run another flush cycle: nothing PENDING remains.
        await worker._flush_outbound(phone)
    check("send called exactly once across two flush cycles", sender.call_count == 1)


async def test_error_classification():
    print("\n[10] Gateway send-error classification")
    # A wrong shared secret or a refused message: no retry can change either.
    check("HTTP 401 (our secret is wrong) is non-retryable",
          classify_send_error(gateway_error(401)).retryable is False)
    check("HTTP 400 (message refused) is non-retryable",
          classify_send_error(gateway_error(400)).retryable is False)
    check("HTTP 404 is non-retryable",
          classify_send_error(gateway_error(404)).retryable is False)
    # WhatsApp not connected yet is exactly what the durable queue exists for.
    check("HTTP 503 (WhatsApp not connected) IS retryable",
          classify_send_error(gateway_error(503)).retryable is True)
    for status in (500, 502, 503):
        check(f"HTTP {status} is retryable",
              classify_send_error(gateway_error(status)).retryable is True)
    check("ConnectionError is retryable",
          classify_send_error(httpx.ConnectError("reset")).retryable is True)
    check("timeout is retryable",
          classify_send_error(httpx.ReadTimeout("t")).retryable is True)
    check("missing config is non-retryable",
          classify_send_error(NotConfigured("not configured")).retryable is False)


async def test_permanent_error_no_retry():
    print("\n[11] 63038 daily limit -> fail immediately, record code, no retries")
    phone = "whatsapp:+910000000012"
    sender = AsyncMock(side_effect=gateway_error(400))
    with patch.object(worker, "respond", new=AsyncMock(return_value="hi")), \
         patch.object(worker, "send_text", sender):
        await worker.enqueue_and_wake("SID-12", phone, "x", 0)
        await drain(phone)
    rows = outbound_rows(phone)
    check("send attempted exactly once (no retries)", sender.call_count == 1)
    check("marked FAILED", rows and rows[0][2] == "FAILED")
    check("the gateway status is recorded for debugging", rows and rows[0][5] == 400)


async def test_retryable_then_success():
    print("\n[12] Transient HTTP 503 retried, then succeeds")
    phone = "whatsapp:+910000000013"
    sender = AsyncMock(side_effect=[gateway_error(503), "wamid.OK"])
    with patch.object(worker, "respond", new=AsyncMock(return_value="hi")), \
         patch.object(worker, "send_text", sender):
        await worker.enqueue_and_wake("SID-13", phone, "x", 0)
        await drain(phone)
    rows = outbound_rows(phone)
    check("retried then SENT", rows and rows[0][2] == "SENT")
    check("sent on second attempt", sender.call_count == 2)
    check("no error code on success", rows and rows[0][5] is None)


async def test_answers_the_latest_message():
    print("\n[15] A burst of messages gets ONE answer, to the latest")
    # Someone firing off three messages while a slow local model is still
    # thinking about the first is mid-thought, not asking three questions.
    # Answering each in turn replies to a conversation that has already moved on.
    phone = "whatsapp:+910000000015"
    seen = []

    async def capture(phone=None, message=None):
        seen.append(message)
        return "one answer"

    sender = AsyncMock(return_value="SID-OUT")
    with patch.object(worker, "respond", new=AsyncMock(side_effect=capture)),          patch.object(worker, "send_text", sender):
        await worker.enqueue_and_wake("SID-15a", phone, "i want biryani", 0)
        await worker.enqueue_and_wake("SID-15b", phone, "actually", 0)
        await worker.enqueue_and_wake("SID-15c", phone, "make it under 300", 0)
        await drain(phone)

    check("the concierge is consulted once, not three times", len(seen) == 1)
    check("the latest message is what it answers",
          seen and seen[0].endswith("make it under 300"))
    check("and the earlier ones ride along as context",
          seen and "i want biryani" in seen[0] and "actually" in seen[0])
    check("exactly one reply is sent", len(outbound_rows(phone)) == 1)
    check("every message is marked DONE - none is stranded",
          inbound_status(phone) == ["DONE", "DONE", "DONE"])


async def test_single_message_unchanged():
    print("\n[16] One message still behaves exactly as before")
    phone = "whatsapp:+910000000016"
    with patch.object(worker, "respond", new=AsyncMock(return_value="just one")),          patch.object(worker, "send_text", AsyncMock(return_value="SID-OUT")):
        await worker.enqueue_and_wake("SID-16", phone, "hey", 0)
        await drain(phone)
    check("answered normally", outbound_rows(phone)[0][1] == "just one")
    check("and marked DONE", inbound_status(phone) == ["DONE"])


async def test_photo_alone_still_answered():
    print("\n[17] A photo with no text still gets an honest answer")
    phone = "whatsapp:+910000000017"
    with patch.object(worker, "respond", new=AsyncMock(return_value="should not run")),          patch.object(worker, "send_text", AsyncMock(return_value="SID-OUT")):
        await worker.enqueue_and_wake("SID-17", phone, "", 1)
        await drain(phone)
    check("the media redirect is used",
          outbound_rows(phone)[0][1] == worker.MEDIA_REDIRECT_MESSAGE)


async def main():
    await test_dedup()
    await test_basic_delivery()
    await test_long_response_ordered()
    await test_send_retry()
    await test_hard_failure_recorded()
    await test_processing_error_still_replies()
    await test_restart_recovery()
    await test_concurrent_phones()
    await test_no_duplicate_resend()
    await test_error_classification()
    await test_permanent_error_no_retry()
    await test_retryable_then_success()
    await test_answers_the_latest_message()
    await test_single_message_unchanged()
    await test_photo_alone_still_answered()

    print("\n" + "=" * 78)
    print(f"RESULT: {_passed} passed, {_failed} failed")
    print("=" * 78)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    finally:
        try:
            os.unlink(_tmp.name)
        except OSError:
            pass
    sys.exit(1 if _failed else 0)
