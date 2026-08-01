"""
Database layer — WhatsApp async delivery queue.

Scope is deliberately tiny: this holds the durable inbound/outbound message
queue that makes WhatsApp replies survive restarts and duplicate webhooks.
The restaurant-ERP schema (inventory, recipes, invoices, purchase orders,
suppliers, sales bills — 21 tables) was removed in the concierge pivot.

Concierge memory (user preferences, order history) lands here in Phase 4.
"""
import sqlite3

DB_PATH = 'database/orders.db'


def get_connection():
    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db():
    conn = get_connection()
    try:
        cursor = conn.cursor()


        # ------------------------------------------------------------------
        # WhatsApp async delivery queue (Phase 2: eliminate lost replies)
        # ------------------------------------------------------------------
        # The webhook persists the inbound message and returns 200 instantly;
        # a background worker processes it via the SAME ConversationEngine and
        # sends the reply via the Twilio REST API. These two tables make that
        # durable across restarts and safe against duplicate webhooks.
        cursor.execute('''
        CREATE TABLE IF NOT EXISTS whatsapp_inbound (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            message_sid TEXT UNIQUE,          -- Twilio MessageSid; dedups retries
            phone TEXT NOT NULL,
            body TEXT,
            num_media INTEGER DEFAULT 0,
            status TEXT DEFAULT 'PENDING',    -- PENDING -> PROCESSING -> DONE / FAILED
            attempts INTEGER DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        ''')
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_wa_inbound_phone_status ON whatsapp_inbound(phone, status)")

        cursor.execute('''
        CREATE TABLE IF NOT EXISTS whatsapp_outbound (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            inbound_id INTEGER,
            phone TEXT NOT NULL,
            part_index INTEGER DEFAULT 0,     -- ordering within a single reply
            body TEXT,
            status TEXT DEFAULT 'PENDING',    -- PENDING -> SENT / FAILED
            attempts INTEGER DEFAULT 0,
            provider_sid TEXT,                -- Twilio message SID once sent
            error_code INTEGER,               -- Twilio error code on failure (e.g. 63038)
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY(inbound_id) REFERENCES whatsapp_inbound(id)
        )
        ''')
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_wa_outbound_phone_status ON whatsapp_outbound(phone, status)")

        # Safe migration for existing DBs created before error_code existed.
        cursor.execute("PRAGMA table_info(whatsapp_outbound)")
        _wo_cols = [row[1] for row in cursor.fetchall()]
        if "error_code" not in _wo_cols:
            cursor.execute("ALTER TABLE whatsapp_outbound ADD COLUMN error_code INTEGER")

        conn.commit()
    finally:
        conn.close()


# ======================================================================
# WhatsApp async delivery queue helpers (Phase 2)
# ======================================================================
# All access to whatsapp_inbound / whatsapp_outbound goes through these
# helpers (per the "use db.py helpers, not raw SQL" project rule).

def enqueue_inbound_message(message_sid, phone, body, num_media=0):
    """Persist an incoming WhatsApp message for async processing.

    Deduplicated by Twilio MessageSid so a retried webhook does not enqueue
    the same message twice. Returns (inbound_id, is_new).
    """
    sid = message_sid or None
    conn = get_connection()
    try:
        cursor = conn.cursor()
        if sid:
            cursor.execute("SELECT id FROM whatsapp_inbound WHERE message_sid = ?", (sid,))
            row = cursor.fetchone()
            if row:
                return row[0], False
        try:
            cursor.execute(
                "INSERT INTO whatsapp_inbound (message_sid, phone, body, num_media, status, attempts) "
                "VALUES (?, ?, ?, ?, 'PENDING', 0)",
                (sid, phone, body, num_media),
            )
            conn.commit()
            return cursor.lastrowid, True
        except sqlite3.IntegrityError:
            # Concurrent duplicate webhook hit the UNIQUE(message_sid) constraint.
            conn.rollback()
            cursor.execute("SELECT id FROM whatsapp_inbound WHERE message_sid = ?", (sid,))
            row = cursor.fetchone()
            return (row[0] if row else None), False
    finally:
        conn.close()


def claim_next_inbound(phone):
    """Atomically claim the oldest PENDING inbound message for a phone and mark
    it PROCESSING. Returns a dict or None. The atomic UPDATE ... WHERE
    status='PENDING' guarantees a message is claimed by exactly one worker."""
    conn = get_connection()
    try:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT id, message_sid, phone, body, num_media, attempts "
            "FROM whatsapp_inbound WHERE phone = ? AND status = 'PENDING' "
            "ORDER BY id ASC LIMIT 1",
            (phone,),
        )
        row = cursor.fetchone()
        if not row:
            return None
        inbound_id = row[0]
        cursor.execute(
            "UPDATE whatsapp_inbound SET status = 'PROCESSING', attempts = attempts + 1, "
            "updated_at = CURRENT_TIMESTAMP WHERE id = ? AND status = 'PENDING'",
            (inbound_id,),
        )
        if cursor.rowcount == 0:
            conn.rollback()
            return None
        conn.commit()
        return {
            "id": inbound_id,
            "message_sid": row[1],
            "phone": row[2],
            "body": row[3],
            "num_media": row[4],
            "attempts": row[5] + 1,
        }
    finally:
        conn.close()


def save_reply_and_finish(inbound_id, phone, parts):
    """Persist the reply parts (ordered) AND mark the inbound message DONE in a
    SINGLE transaction. Doing both atomically means a crash can never leave a
    message marked done with no reply queued, nor a reply queued twice."""
    conn = get_connection()
    try:
        cursor = conn.cursor()
        for idx, body in enumerate(parts):
            cursor.execute(
                "INSERT INTO whatsapp_outbound (inbound_id, phone, part_index, body, status, attempts) "
                "VALUES (?, ?, ?, ?, 'PENDING', 0)",
                (inbound_id, phone, idx, body),
            )
        cursor.execute(
            "UPDATE whatsapp_inbound SET status = 'DONE', updated_at = CURRENT_TIMESTAMP WHERE id = ?",
            (inbound_id,),
        )
        conn.commit()
    finally:
        conn.close()


def save_error_reply(inbound_id, phone, text):
    """Queue a single error reply and mark the inbound message FAILED. The
    failure is recorded (not silently dropped) and the user still gets a note."""
    conn = get_connection()
    try:
        cursor = conn.cursor()
        cursor.execute(
            "INSERT INTO whatsapp_outbound (inbound_id, phone, part_index, body, status, attempts) "
            "VALUES (?, ?, 0, ?, 'PENDING', 0)",
            (inbound_id, phone, text),
        )
        cursor.execute(
            "UPDATE whatsapp_inbound SET status = 'FAILED', updated_at = CURRENT_TIMESTAMP WHERE id = ?",
            (inbound_id,),
        )
        conn.commit()
    finally:
        conn.close()


def get_pending_outbound(phone):
    """Return this phone's unsent reply parts in strict delivery order
    (by inbound message, then part index)."""
    conn = get_connection()
    try:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT id, inbound_id, part_index, body, attempts FROM whatsapp_outbound "
            "WHERE phone = ? AND status = 'PENDING' ORDER BY inbound_id ASC, part_index ASC",
            (phone,),
        )
        return [
            {"id": r[0], "inbound_id": r[1], "part_index": r[2], "body": r[3], "attempts": r[4]}
            for r in cursor.fetchall()
        ]
    finally:
        conn.close()


def increment_outbound_attempt(outbound_id):
    conn = get_connection()
    try:
        cursor = conn.cursor()
        cursor.execute(
            "UPDATE whatsapp_outbound SET attempts = attempts + 1, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
            (outbound_id,),
        )
        conn.commit()
    finally:
        conn.close()


def mark_outbound_sent(outbound_id, provider_sid):
    conn = get_connection()
    try:
        cursor = conn.cursor()
        cursor.execute(
            "UPDATE whatsapp_outbound SET status = 'SENT', provider_sid = ?, "
            "updated_at = CURRENT_TIMESTAMP WHERE id = ?",
            (provider_sid, outbound_id),
        )
        conn.commit()
    finally:
        conn.close()


def mark_outbound_failed(outbound_id, error_code=None):
    conn = get_connection()
    try:
        cursor = conn.cursor()
        cursor.execute(
            "UPDATE whatsapp_outbound SET status = 'FAILED', error_code = ?, "
            "updated_at = CURRENT_TIMESTAMP WHERE id = ?",
            (error_code, outbound_id),
        )
        conn.commit()
    finally:
        conn.close()


def has_pending_work(phone):
    """True if this phone has any inbound left to process or outbound left to
    send. Used by the worker to decide (under lock) whether it may exit."""
    conn = get_connection()
    try:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT 1 FROM whatsapp_inbound WHERE phone = ? AND status = 'PENDING' LIMIT 1",
            (phone,),
        )
        if cursor.fetchone():
            return True
        cursor.execute(
            "SELECT 1 FROM whatsapp_outbound WHERE phone = ? AND status = 'PENDING' LIMIT 1",
            (phone,),
        )
        return cursor.fetchone() is not None
    finally:
        conn.close()


def get_phones_with_pending_work():
    """Distinct phones that still have queued inbound or outbound work — used to
    respawn workers on startup (restart recovery)."""
    conn = get_connection()
    try:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT phone FROM whatsapp_inbound WHERE status = 'PENDING' "
            "UNION SELECT phone FROM whatsapp_outbound WHERE status = 'PENDING'"
        )
        return [r[0] for r in cursor.fetchall()]
    finally:
        conn.close()


def reset_interrupted_inbound():
    """On startup, any inbound stuck in PROCESSING was interrupted mid-engine by
    a crash. We mark it FAILED rather than blindly reprocessing it — reprocessing
    could re-run non-idempotent side effects (e.g. placing an order twice). The
    failure is recorded (not silent). Returns the number reset."""
    conn = get_connection()
    try:
        cursor = conn.cursor()
        cursor.execute(
            "UPDATE whatsapp_inbound SET status = 'FAILED', updated_at = CURRENT_TIMESTAMP "
            "WHERE status = 'PROCESSING'"
        )
        conn.commit()
        return cursor.rowcount
    finally:
        conn.close()


if __name__ == '__main__':
    init_db()
    print('Database initialized successfully.')