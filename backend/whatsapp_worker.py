"""
Async WhatsApp delivery worker (Phase 2: eliminate lost replies).

WHY THIS EXISTS
---------------
A webhook must answer in milliseconds. Meta redelivers the whole batch if it
does not, and an LLM turn plus provider calls takes far longer than any webhook
timeout allows — so replying inside the webhook loses replies. This worker
decouples processing from the webhook:

    webhook  ->  persist inbound + return 200 immediately (<1s)
    worker   ->  ai.concierge.respond -> reply -> WhatsApp Cloud API

Design guarantees:
  * SINGLE source of truth — the worker calls `ai.concierge.respond` and holds
    no product logic of its own.
  * Ordering — exactly ONE worker task per phone drains that phone's messages in
    arrival order; reply parts are sent in part_index order.
  * No duplicate replies — inbound is deduped by provider message id; a reply
    is queued
    once (atomically with marking the inbound DONE) and each part is sent only
    while PENDING.
  * No lost replies — everything is persisted before the webhook returns 200;
    sends are retried; hard failures are marked FAILED and logged, never
    silently discarded.
  * Restart recovery — on startup, pending outbound is re-sent and phones with
    queued work get their workers respawned.
  * No race conditions — the per-phone worker registry and each worker's
    exit decision are serialized under one asyncio lock; message visibility is
    guaranteed because the webhook commits the inbound row before waking a
    worker.
"""
import asyncio

from core.logger import logger
import db
from ai.concierge import respond
from whatsapp import classify_send_error, send_text


# Reply parts are re-split with the SAME limit the old webhook used, so delivery
# is byte-for-byte identical to the previous behaviour (imported lazily to avoid
# a circular import with backend.routes).
_MAX_PART_LEN = 1500

MAX_SEND_ATTEMPTS = 5
SEND_BACKOFF_BASE_SECONDS = 2

# Attachments aren't understood yet (images/voice land in a later phase).
MEDIA_REDIRECT_MESSAGE = (
    "I can't open attachments yet — but tell me what you're in the mood for and "
    "I'll take it from there 🙂"
)

# Never name the product or the failure in an apology: the user has no use for
# either, and a stack-trace tone erodes trust faster than the fault itself.
_FALLBACK_REPLY = "Sorry — I lost my train of thought there. Say that again?"
_ERROR_REPLY = "Something went wrong on my end — mind trying that again?"

# phone -> asyncio.Task. Guarded by _registry_lock together with each worker's
# exit decision so a message can never be stranded without a running worker.
_workers = {}
_registry_lock = asyncio.Lock()


def _split(reply):
    from backend.routes import split_message  # lazy: breaks routes<->worker cycle
    return split_message(reply, max_length=_MAX_PART_LEN)


# ----------------------------------------------------------------------
# Public API used by the webhook and by startup recovery
# ----------------------------------------------------------------------
async def enqueue_and_wake(message_sid, phone, body, num_media=0):
    """Persist an inbound message and ensure a worker is draining this phone.
    Returns (inbound_id, is_new). Safe to call from the webhook — it does only a
    single INSERT then (maybe) spawns a task, so it returns in ~milliseconds."""
    inbound_id, is_new = db.enqueue_inbound_message(message_sid, phone, body, num_media)
    # Even if not new (duplicate webhook), make sure a worker exists in case the
    # previous one already exited; it will simply find nothing to do.
    await _ensure_worker(phone)
    return inbound_id, is_new


async def deliver(phone, text):
    """Send a message nobody asked for, through the normal delivery path.

    Used by work that finishes on its own clock — a payment confirming after
    the turn that started it has ended. Goes through the outbound queue so it
    is ordered behind anything already waiting, retried on failure, and
    recovered after a restart.
    """
    outbound_id = db.enqueue_followup(phone, text)
    await _ensure_worker(phone)
    return outbound_id


async def recover_pending():
    """Startup recovery: fail interrupted in-flight messages (do not blindly
    reprocess side effects), then respawn workers for any phone that still has
    queued inbound or unsent outbound work."""
    interrupted = db.reset_interrupted_inbound()
    if interrupted:
        logger.error(
            f"[whatsapp_worker] {interrupted} inbound message(s) were interrupted "
            f"by a restart and marked FAILED (not reprocessed, to avoid duplicate "
            f"side effects)."
        )
    phones = db.get_phones_with_pending_work()
    for phone in phones:
        await _ensure_worker(phone)
    if phones:
        logger.info(f"[whatsapp_worker] recovery respawned workers for {len(phones)} phone(s).")


# ----------------------------------------------------------------------
# Worker registry / lifecycle
# ----------------------------------------------------------------------
async def _ensure_worker(phone):
    async with _registry_lock:
        task = _workers.get(phone)
        if task and not task.done():
            return
        _workers[phone] = asyncio.create_task(_worker_loop(phone))


async def _worker_loop(phone):
    """Drain one phone's queue, then exit — but the exit decision is made under
    the registry lock so a message enqueued concurrently is never stranded."""
    while True:
        try:
            # Flush first: re-send anything left over from a prior crash/attempt.
            await _flush_outbound(phone)

            while True:
                batch = db.claim_pending_inbound(phone)
                if not batch:
                    break
                await _process_inbound(batch)
                await _flush_outbound(phone)
        except Exception:
            logger.error(f"[whatsapp_worker] worker loop error for {phone}", exc_info=True)

        # Exit only if there is provably no more work; re-check under the lock so
        # a concurrent enqueue_and_wake either sees this task alive or spawns a
        # fresh one after we remove ourselves.
        async with _registry_lock:
            if not db.has_pending_work(phone):
                _workers.pop(phone, None)
                return
        # else: more work arrived — loop again.


# ----------------------------------------------------------------------
# Processing + sending
# ----------------------------------------------------------------------
async def _process_inbound(batch):
    """Answer everything waiting for one person as a SINGLE turn.

    Whoever sent three messages while the model was still thinking about the
    first is mid-thought, not asking three questions. Replying to each in turn
    answers a conversation that has already moved on — and with a slow local
    model, the first reply lands after the user has changed their mind twice.

    So the reply belongs to the LATEST message, and the earlier ones ride along
    as context. All of them are marked done; only one answer goes out.
    """
    phone = batch[0]["phone"]
    latest = batch[-1]
    earlier = [m["id"] for m in batch[:-1]]

    # Attachments carry no text to fold in, so they only decide the reply when
    # there is nothing to read at all.
    said = [m["body"].strip() for m in batch if (m["body"] or "").strip()]
    body = "\n".join(said)

    if len(batch) > 1:
        logger.info(
            f"[whatsapp_worker] answering {len(batch)} queued messages from "
            f"{phone} as one turn"
        )

    try:
        if not body and any(m.get("num_media") for m in batch):
            reply = MEDIA_REDIRECT_MESSAGE
        else:
            reply = await respond(phone=phone, message=body)
        if not reply:
            reply = _FALLBACK_REPLY
        parts = _split(reply)
        # The earlier messages are settled first: if the reply write then fails,
        # the retry answers the latest message rather than replaying the lot.
        db.finish_inbound(earlier)
        # Persist reply parts AND mark inbound DONE atomically.
        db.save_reply_and_finish(latest["id"], phone, parts)
    except Exception:
        logger.error(
            f"[whatsapp_worker] processing failed for inbound={latest['id']}",
            exc_info=True,
        )
        # Record the failure and still deliver a note — never leave the user hung.
        try:
            db.finish_inbound(earlier)
            db.save_error_reply(latest["id"], phone, _ERROR_REPLY)
        except Exception:
            logger.error("[whatsapp_worker] could not queue error reply", exc_info=True)


async def _flush_outbound(phone):
    """Send every PENDING reply part for this phone, in order."""
    for part in db.get_pending_outbound(phone):
        await _send_with_retry(part, phone)


async def _send_with_retry(part, phone):
    outbound_id = part["id"]
    body = part["body"]
    for attempt in range(1, MAX_SEND_ATTEMPTS + 1):
        db.increment_outbound_attempt(outbound_id)
        try:
            sid = await send_text(phone, body)
            db.mark_outbound_sent(outbound_id, sid)
            return
        except Exception as e:
            klass = classify_send_error(e)

            # Permanent error (e.g. 63038 daily limit, auth/sender/recipient):
            # retrying cannot help, so fail immediately and record the code.
            if not klass.retryable:
                db.mark_outbound_failed(outbound_id, error_code=klass.code)
                logger.error(
                    f"[whatsapp_worker] PERMANENT send failure outbound={outbound_id} "
                    f"— {klass.reason}; not retrying."
                )
                return

            logger.error(
                f"[whatsapp_worker] send failed outbound={outbound_id} "
                f"attempt={attempt}/{MAX_SEND_ATTEMPTS} — {klass.reason}."
            )
            if attempt < MAX_SEND_ATTEMPTS:
                await asyncio.sleep(SEND_BACKOFF_BASE_SECONDS * attempt)
            else:
                db.mark_outbound_failed(outbound_id, error_code=klass.code)
                logger.error(
                    f"[whatsapp_worker] GIVING UP on outbound={outbound_id} after "
                    f"{MAX_SEND_ATTEMPTS} attempts — {klass.reason}; marked FAILED "
                    f"(recorded, not silently dropped)."
                )
