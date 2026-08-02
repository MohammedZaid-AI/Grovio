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

        # ------------------------------------------------------------------
        # User model (Phase 3)
        # ------------------------------------------------------------------
        # Identity is the WhatsApp phone number — there is no signup, no
        # password, no profile screen. Everything the concierge knows about a
        # person hangs off that one key.

        # Long-term preferences as key/value so a new preference type (gym days,
        # health goal, spice tolerance...) never needs a migration.
        cursor.execute('''
        CREATE TABLE IF NOT EXISTS user_facts (
            phone TEXT NOT NULL,
            key TEXT NOT NULL,
            value TEXT,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (phone, key)
        )
        ''')

        # Durable conversation history (the in-memory session is wiped on restart).
        cursor.execute('''
        CREATE TABLE IF NOT EXISTS conversation_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            phone TEXT NOT NULL,
            role TEXT NOT NULL,               -- 'user' | 'assistant'
            content TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        ''')
        cursor.execute(
            "CREATE INDEX IF NOT EXISTS idx_history_phone ON conversation_history(phone, id)"
        )

        # Food memory: what they ate, what they loved, what they turned down.
        cursor.execute('''
        CREATE TABLE IF NOT EXISTS food_memory (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            phone TEXT NOT NULL,
            item TEXT NOT NULL,
            venue TEXT,
            sentiment TEXT NOT NULL,          -- ORDERED | LIKED | DISLIKED | REJECTED
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        ''')
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_food_memory_phone ON food_memory(phone)")

        # ------------------------------------------------------------------
        # Identity + provider account linking (Phase 4)
        # ------------------------------------------------------------------
        cursor.execute('''
        CREATE TABLE IF NOT EXISTS users (
            phone TEXT PRIMARY KEY,
            display_name TEXT,
            onboarding_status TEXT DEFAULT 'NEW',   -- NEW | LINKED | COMPLETE
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        ''')

        # One row per (user, provider). Tokens are ENCRYPTED — see core/crypto.py.
        # Separate from users so revoking one provider cannot disturb another.
        cursor.execute('''
        CREATE TABLE IF NOT EXISTS provider_links (
            phone TEXT NOT NULL,
            provider TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'LINKED',  -- LINKED | REVOKED
            access_token TEXT,                      -- ENCRYPTED
            refresh_token TEXT,                     -- ENCRYPTED
            expires_at TIMESTAMP,                   -- NULL = does not expire
            scope TEXT,
            client_id TEXT,
            linked_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (phone, provider)
        )
        ''')

        # In-flight OAuth authorisations. Rows are single-use and short-lived;
        # keeping them in SQLite (not memory) is what lets a link survive a
        # restart and lets us resume the user's original request afterwards.
        cursor.execute('''
        CREATE TABLE IF NOT EXISTS oauth_states (
            state TEXT PRIMARY KEY,
            phone TEXT NOT NULL,
            provider TEXT NOT NULL,
            code_verifier TEXT,                     -- ENCRYPTED (PKCE)
            pending_message TEXT,                   -- resumed after linking
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            expires_at TIMESTAMP NOT NULL,
            used_at TIMESTAMP                       -- NULL until consumed
        )
        ''')

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


# ======================================================================
# User model helpers (Phase 3)
# ======================================================================

def get_user_facts(phone):
    """All long-term preferences for a user as {key: value}."""
    conn = get_connection()
    try:
        rows = conn.execute(
            "SELECT key, value FROM user_facts WHERE phone = ? ORDER BY key", (phone,)
        ).fetchall()
        return {key: value for key, value in rows}
    finally:
        conn.close()


def set_user_fact(phone, key, value):
    """Upsert one preference. Storing an empty value forgets it."""
    key = (key or "").strip().lower()
    if not key:
        return
    conn = get_connection()
    try:
        if value is None or not str(value).strip():
            conn.execute("DELETE FROM user_facts WHERE phone = ? AND key = ?", (phone, key))
        else:
            conn.execute(
                "INSERT INTO user_facts (phone, key, value, updated_at) "
                "VALUES (?, ?, ?, CURRENT_TIMESTAMP) "
                "ON CONFLICT(phone, key) DO UPDATE SET "
                "value = excluded.value, updated_at = CURRENT_TIMESTAMP",
                (phone, key, str(value).strip()),
            )
        conn.commit()
    finally:
        conn.close()


def add_history(phone, role, content):
    conn = get_connection()
    try:
        conn.execute(
            "INSERT INTO conversation_history (phone, role, content) VALUES (?, ?, ?)",
            (phone, role, content),
        )
        conn.commit()
    finally:
        conn.close()


def get_history(phone, limit=20):
    """The most recent turns, oldest first (ready to append to a prompt)."""
    conn = get_connection()
    try:
        rows = conn.execute(
            "SELECT role, content FROM conversation_history WHERE phone = ? "
            "ORDER BY id DESC LIMIT ?",
            (phone, limit),
        ).fetchall()
        return [{"role": role, "content": content} for role, content in reversed(rows)]
    finally:
        conn.close()


def add_food_memory(phone, item, sentiment, venue=None):
    conn = get_connection()
    try:
        conn.execute(
            "INSERT INTO food_memory (phone, item, venue, sentiment) VALUES (?, ?, ?, ?)",
            (phone, item, venue, sentiment.upper()),
        )
        conn.commit()
    finally:
        conn.close()


def get_food_memory(phone, limit=50):
    conn = get_connection()
    try:
        rows = conn.execute(
            "SELECT item, venue, sentiment, created_at FROM food_memory "
            "WHERE phone = ? ORDER BY id DESC LIMIT ?",
            (phone, limit),
        ).fetchall()
        return [
            {"item": item, "venue": venue, "sentiment": sentiment, "at": at}
            for item, venue, sentiment, at in rows
        ]
    finally:
        conn.close()


# ======================================================================
# Identity + provider linking helpers (Phase 4)
# ======================================================================
# Tokens arrive here ALREADY ENCRYPTED. This layer stores bytes; it does not
# know what they mean. Encryption lives in core/crypto.py, policy in
# ai/providers/vault.py.

def get_or_create_user(phone):
    """Idempotent. Returns the user row as a dict."""
    conn = get_connection()
    try:
        conn.execute(
            "INSERT INTO users (phone) VALUES (?) ON CONFLICT(phone) DO NOTHING", (phone,)
        )
        conn.commit()
        row = conn.execute(
            "SELECT phone, display_name, onboarding_status, created_at, updated_at "
            "FROM users WHERE phone = ?", (phone,)
        ).fetchone()
    finally:
        conn.close()
    keys = ("phone", "display_name", "onboarding_status", "created_at", "updated_at")
    return dict(zip(keys, row))


def update_user(phone, **fields):
    """Update display_name and/or onboarding_status."""
    allowed = {"display_name", "onboarding_status"}
    fields = {k: v for k, v in fields.items() if k in allowed}
    if not fields:
        return
    assignments = ", ".join(f"{k} = ?" for k in fields)
    conn = get_connection()
    try:
        conn.execute(
            f"UPDATE users SET {assignments}, updated_at = CURRENT_TIMESTAMP WHERE phone = ?",
            (*fields.values(), phone),
        )
        conn.commit()
    finally:
        conn.close()


def save_provider_link(phone, provider, access_token, refresh_token,
                       expires_at, scope=None, client_id=None):
    """Upsert one provider link. Re-linking overwrites — never a second row."""
    conn = get_connection()
    try:
        conn.execute(
            "INSERT INTO provider_links "
            "(phone, provider, status, access_token, refresh_token, expires_at, scope, client_id) "
            "VALUES (?, ?, 'LINKED', ?, ?, ?, ?, ?) "
            "ON CONFLICT(phone, provider) DO UPDATE SET "
            "status='LINKED', access_token=excluded.access_token, "
            "refresh_token=excluded.refresh_token, expires_at=excluded.expires_at, "
            "scope=excluded.scope, client_id=excluded.client_id, "
            "updated_at=CURRENT_TIMESTAMP",
            (phone, provider, access_token, refresh_token, expires_at, scope, client_id),
        )
        conn.commit()
    finally:
        conn.close()


def get_provider_link(phone, provider):
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT phone, provider, status, access_token, refresh_token, expires_at, "
            "scope, client_id FROM provider_links WHERE phone = ? AND provider = ?",
            (phone, provider),
        ).fetchone()
    finally:
        conn.close()
    if not row:
        return None
    keys = ("phone", "provider", "status", "access_token", "refresh_token",
            "expires_at", "scope", "client_id")
    return dict(zip(keys, row))


def get_linked_providers(phone):
    """Names of providers currently usable by this user."""
    conn = get_connection()
    try:
        rows = conn.execute(
            "SELECT provider FROM provider_links WHERE phone = ? AND status = 'LINKED' "
            "ORDER BY provider", (phone,)
        ).fetchall()
    finally:
        conn.close()
    return [r[0] for r in rows]


def revoke_provider_link(phone, provider, delete=False):
    """Mark a link unusable. `delete=True` also removes the stored tokens,
    which is what an explicit unlink should do."""
    conn = get_connection()
    try:
        if delete:
            conn.execute(
                "DELETE FROM provider_links WHERE phone = ? AND provider = ?", (phone, provider)
            )
        else:
            conn.execute(
                "UPDATE provider_links SET status='REVOKED', access_token=NULL, "
                "refresh_token=NULL, updated_at=CURRENT_TIMESTAMP "
                "WHERE phone = ? AND provider = ?",
                (phone, provider),
            )
        conn.commit()
    finally:
        conn.close()


def save_oauth_state(state, phone, provider, code_verifier, pending_message, expires_at):
    conn = get_connection()
    try:
        conn.execute(
            "INSERT INTO oauth_states "
            "(state, phone, provider, code_verifier, pending_message, expires_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (state, phone, provider, code_verifier, pending_message, expires_at),
        )
        conn.commit()
    finally:
        conn.close()


def claim_oauth_state(state):
    """Atomically consume a state token exactly once.

    SECURITY: the single-use claim is what prevents replay. A second callback
    carrying the same state finds used_at already set and gets nothing back.
    Returns the row dict, or None if unknown/already used.
    """
    conn = get_connection()
    try:
        cursor = conn.cursor()
        cursor.execute(
            "UPDATE oauth_states SET used_at = CURRENT_TIMESTAMP "
            "WHERE state = ? AND used_at IS NULL",
            (state,),
        )
        if cursor.rowcount == 0:
            conn.rollback()
            return None
        row = cursor.execute(
            "SELECT state, phone, provider, code_verifier, pending_message, expires_at "
            "FROM oauth_states WHERE state = ?", (state,)
        ).fetchone()
        conn.commit()
    finally:
        conn.close()
    keys = ("state", "phone", "provider", "code_verifier", "pending_message", "expires_at")
    return dict(zip(keys, row))


def delete_expired_oauth_states():
    """Housekeeping: drop states that were never completed."""
    conn = get_connection()
    try:
        cursor = conn.cursor()
        cursor.execute("DELETE FROM oauth_states WHERE expires_at < CURRENT_TIMESTAMP")
        conn.commit()
        return cursor.rowcount
    finally:
        conn.close()


if __name__ == '__main__':
    init_db()
    print('Database initialized successfully.')