"""
SAT adversarial probes — attempts to BREAK Grovio at the boundaries that the
existing suites don't cover end-to-end. Read-only w.r.t. production code.

Network is mocked (engine.process) so these exercise the webhook/auth/state
machinery, not Groq/Swiggy.
"""
import os
import sys
import tempfile
import datetime
from unittest.mock import AsyncMock, patch

sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.setdefault("JWT_SECRET", "x" * 48)
os.environ.setdefault("TWILIO_AUTH_TOKEN", "sat-probe-token")
os.environ.setdefault("DASHBOARD_PASSWORD", "sat-pass")

import db
_tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
_tmp.close()
db.DB_PATH = _tmp.name
db.init_db()

import jwt as pyjwt
from fastapi.testclient import TestClient
from twilio.request_validator import RequestValidator

from backend.conversation_engine import engine
import backend.routes as routes
from backend.app import app
from backend.routes import split_message, JWT_SECRET, JWT_ALGORITHM

_passed = 0
_failed = 0
_bugs = []


def check(name, condition, bug=None):
    global _passed, _failed
    if condition:
        _passed += 1
        print(f"  ✅ {name}")
    else:
        _failed += 1
        print(f"  ❌ {name}")
        if bug:
            _bugs.append(bug)


def signed(client, data):
    validator = RequestValidator(os.environ["TWILIO_AUTH_TOKEN"])
    url = "http://testserver/webhook"
    sig = validator.compute_signature(url, data)
    return client.post("/webhook", data=data, headers={"X-Twilio-Signature": sig})


def inbound_count(phone):
    conn = db.get_connection()
    try:
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*) FROM whatsapp_inbound WHERE phone = ?", (phone,))
        return cur.fetchone()[0]
    finally:
        conn.close()


def probe_webhook_replay():
    print("\n[P1] Duplicate/replayed webhook must not enqueue twice")
    with patch.object(engine, "process", new=AsyncMock(return_value="ok")):
        with TestClient(app) as client:
            data = {"Body": "1 coke", "From": "whatsapp:+915550001", "MessageSid": "SM-REPLAY-1"}
            r1 = signed(client, data)
            r2 = signed(client, data)  # exact replay
            r3 = signed(client, data)  # again
    check("all replays return 200", r1.status_code == 200 and r2.status_code == 200 and r3.status_code == 200)
    check("only ONE inbound row for replayed MessageSid", inbound_count("whatsapp:+915550001") == 1,
          bug="Webhook replay creates duplicate inbound rows")


def probe_webhook_signature():
    print("\n[P2] Malformed / unsigned webhooks fail closed")
    with patch.object(engine, "process", new=AsyncMock(return_value="ok")):
        with TestClient(app) as client:
            # No signature header.
            r_nosig = client.post("/webhook", data={"Body": "hi", "From": "whatsapp:+9", "MessageSid": "SM-X"})
            # Wrong signature.
            r_badsig = client.post("/webhook", data={"Body": "hi", "From": "whatsapp:+9", "MessageSid": "SM-Y"},
                                   headers={"X-Twilio-Signature": "deadbeef"})
            # Valid signature but empty Body/From (malformed-but-authentic) must not 500.
            r_empty = signed(client, {"Body": "", "From": "", "MessageSid": "SM-EMPTY"})
    check("missing signature rejected (403)", r_nosig.status_code == 403)
    check("bad signature rejected (403)", r_badsig.status_code == 403)
    check("authentic empty message does not crash (200)", r_empty.status_code == 200,
          bug="Empty authentic webhook 500s")


def probe_jwt():
    print("\n[P3] JWT tampering / forgery / expiry rejected")
    with TestClient(app) as client:
        # Forged with wrong secret.
        forged = pyjwt.encode({"sub": "admin"}, "wrong-secret", algorithm=JWT_ALGORITHM)
        r_forged = client.get("/admin/inventory", cookies={"session_token": forged})
        # Tampered payload (alg none style) — random junk.
        r_junk = client.get("/admin/inventory", cookies={"session_token": "not.a.jwt"})
        # Expired but correctly signed.
        expired = pyjwt.encode(
            {"sub": "admin", "exp": datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(hours=1)},
            JWT_SECRET, algorithm=JWT_ALGORITHM,
        )
        r_expired = client.get("/admin/inventory", cookies={"session_token": expired})
        # No cookie at all.
        r_none = client.get("/admin/inventory")
    check("forged (wrong secret) rejected 401", r_forged.status_code == 401,
          bug="Forged JWT accepted")
    check("junk token rejected 401", r_junk.status_code == 401)
    check("expired token rejected 401", r_expired.status_code == 401,
          bug="Expired JWT accepted")
    check("no cookie rejected 401", r_none.status_code == 401)


def probe_conversation_state():
    print("\n[P4] Shopping session cancel / restart / cleanup")
    from ai.shopping.shopping_session import shopping_session
    ph = "whatsapp:+915550004"
    shopping_session.start(ph, [{"name": "coke", "quantity": 1}])
    check("session exists after start", shopping_session.has_session(ph))
    shopping_session.end(ph)
    check("session cleared after end (cancel)", not shopping_session.has_session(ph))
    # Restart immediately -> fresh session, no leaked selected items.
    shopping_session.start(ph, [{"name": "milk", "quantity": 2}])
    check("restart yields fresh empty selection", shopping_session.selected(ph) == [])
    check("restart lifecycle is draft", shopping_session.lifecycle(ph) == "draft")
    shopping_session.end(ph)


def probe_duplicate_checkout_guard():
    print("\n[P5] Double-confirm cannot place two orders")
    from ai.shopping.shopping_session import shopping_session
    ph = "whatsapp:+915550005"
    shopping_session.start(ph, [{"name": "coke", "quantity": 1}])
    first = shopping_session.begin_checkout(ph)
    second = shopping_session.begin_checkout(ph)
    check("first checkout allowed", first is True)
    check("second checkout blocked (no duplicate order)", second is False,
          bug="Duplicate checkout not prevented")
    shopping_session.end(ph)


def probe_split_message_giant_word():
    print("\n[P6] Chunking must never emit a part over the WhatsApp limit")
    # Realistic long reply (with separators) — the normal case.
    normal = "\n".join(f"item {i}" for i in range(500))
    parts_normal = split_message(normal, 1500)
    check("normal long reply: all parts <= 1500", all(len(p) <= 1500 for p in parts_normal))
    # Pathological: a single unbroken token far larger than the limit.
    giant = "A" * 4000
    parts_giant = split_message(giant, 1500)
    check("giant unbroken token: all parts <= 1500", all(len(p) <= 1500 for p in parts_giant),
          bug="split_message does not hard-split an unbroken >limit token; a part can exceed "
              "WhatsApp's 1600-char cap and be rejected by Twilio")


def probe_negative_inventory():
    print("\n[P7] Inventory guards reject negative / bad values")
    # Authenticate.
    with TestClient(app) as client:
        login = client.post("/admin/login", data={"password": os.environ["DASHBOARD_PASSWORD"]})
        token = login.cookies.get("session_token")
        cookies = {"session_token": token} if token else {}
        # Seed a product.
        client.post("/admin/inventory/add", json={"product_name": "SATOil", "current_stock": 5,
                    "minimum_stock": 1, "unit": "litre"}, cookies=cookies)
        # Negative absolute stock.
        r_neg = client.post("/admin/inventory/update", json={"product_name": "SATOil",
                            "new_stock": -3, "action_type": "SET_ABSOLUTE"}, cookies=cookies)
        # Delta that drives below zero.
        r_under = client.post("/admin/inventory/update", json={"product_name": "SATOil",
                             "new_stock": -999, "action_type": "ADJUST_DELTA"}, cookies=cookies)
        # Non-numeric.
        r_nan = client.post("/admin/inventory/update", json={"product_name": "SATOil",
                           "new_stock": "abc", "action_type": "SET_ABSOLUTE"}, cookies=cookies)
    check("login succeeded", bool(token))
    check("negative absolute stock rejected 400", r_neg.status_code == 400,
          bug="Negative absolute stock accepted")
    check("delta below zero rejected 400", r_under.status_code == 400,
          bug="Adjustment below zero accepted")
    check("non-numeric stock rejected 400", r_nan.status_code == 400)


if __name__ == "__main__":
    try:
        probe_webhook_replay()
        probe_webhook_signature()
        probe_jwt()
        probe_conversation_state()
        probe_duplicate_checkout_guard()
        probe_split_message_giant_word()
        probe_negative_inventory()
    finally:
        pass

    print("\n" + "=" * 78)
    print(f"RESULT: {_passed} passed, {_failed} failed")
    if _bugs:
        print("POTENTIAL BUGS:")
        for b in _bugs:
            print(f"  - {b}")
    print("=" * 78)
    try:
        os.unlink(_tmp.name)
    except OSError:
        pass
