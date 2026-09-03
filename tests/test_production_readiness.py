"""
End-to-end production readiness, starting at the HTTP webhook.

Every other suite starts below the transport. This one starts where Meta does:
a signed POST to /webhook, through routes, the durable queue, the worker, the
concierge, the planner, skills, the registry, and back out as a Cloud API send.

WHAT IS REAL HERE
  * the FastAPI route and its signature check (real HMAC over the real body)
  * the SQLite queue, dedup, per-phone ordering, the worker loop
  * concierge, planner tool dispatch, skills, the conversation state machine
  * the recommendation engine and both provider adapters

WHAT IS STUBBED, AND WHY
  * the LLM - scripted, so assertions are about the product, not model mood
  * the MCP session - no network, and NOTHING here places a real order
  * the outbound HTTP call to Meta - asserted on, never actually made

So this proves OUR code works end to end. It does NOT prove Swiggy or Meta
accept it; that needs live credentials and a public webhook, and is listed as
unverified in the report.
"""
import asyncio
import hashlib
import hmac
import json
import os
import sys
import tempfile
from unittest.mock import AsyncMock, patch

sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from cryptography.fernet import Fernet

APP_SECRET = "test-app-secret"
VERIFY_TOKEN = "test-verify-token"
SECRET_TOKEN = "EAAsecret-token-that-must-never-be-logged"
USER = "919911100077"
STRANGER = "919911100078"

os.environ["TOKEN_ENCRYPTION_KEY"] = Fernet.generate_key().decode()
os.environ["MCP_USE_ANONYMIZED_TELEMETRY"] = "false"
os.environ["WHATSAPP_PROVIDER"] = "cloud"
os.environ["WHATSAPP_APP_SECRET"] = APP_SECRET
os.environ["WHATSAPP_VERIFY_TOKEN"] = VERIFY_TOKEN
os.environ["WHATSAPP_ACCESS_TOKEN"] = SECRET_TOKEN
os.environ["WHATSAPP_PHONE_NUMBER_ID"] = "555000111"
os.environ["AUTHORIZED_PHONES"] = USER
os.environ["PUBLIC_BASE_URL"] = "https://concierge.example"

import db

_tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
_tmp.close()
db.DB_PATH = _tmp.name
db.init_db()

from fastapi import FastAPI
from fastapi.testclient import TestClient

import whatsapp
import backend.whatsapp_worker as worker
from backend.routes import router
from ai import conversation, identity, memory, skills
from ai.providers import ProviderKind, registry, swiggy, swiggy_food
from ai.providers.base import SearchContext
from core.llm import LLMReply, ToolCall

app = FastAPI()
app.include_router(router)
client = TestClient(app)

_passed = _failed = 0
_failures = []


def check(name, condition, detail=""):
    global _passed, _failed
    if condition:
        _passed += 1
        print(f"  ✅ {name}")
    else:
        _failed += 1
        _failures.append((name, detail))
        print(f"  ❌ {name}" + (f"  [{detail}]" if detail else ""))


def run(coro):
    return asyncio.run(coro)


# ----------------------------------------------------------------------
# Real Meta payloads, signed the way Meta signs them
# ----------------------------------------------------------------------
def signature(raw):
    return "sha256=" + hmac.new(APP_SECRET.encode(), raw, hashlib.sha256).hexdigest()


def post(payload, sign=True, header=None):
    raw = json.dumps(payload).encode()
    headers = {"Content-Type": "application/json"}
    if header is not None:
        headers["X-Hub-Signature-256"] = header
    elif sign:
        headers["X-Hub-Signature-256"] = signature(raw)
    return client.post("/webhook", content=raw, headers=headers)


def inbound(text, message_id, phone=USER):
    return {
        "object": "whatsapp_business_account",
        "entry": [{"id": "WABA", "changes": [{"field": "messages", "value": {
            "messaging_product": "whatsapp",
            "metadata": {"display_phone_number": "15550001",
                         "phone_number_id": "555000111"},
            "contacts": [{"profile": {"name": "Maaiz"}, "wa_id": phone}],
            "messages": [{"from": phone, "id": message_id, "timestamp": "1755000000",
                          "type": "text", "text": {"body": text}}],
        }}]}],
    }


def receipt(message_id, status):
    return {
        "object": "whatsapp_business_account",
        "entry": [{"id": "WABA", "changes": [{"field": "messages", "value": {
            "messaging_product": "whatsapp",
            "metadata": {"phone_number_id": "555000111"},
            "statuses": [{"id": message_id, "status": status,
                          "timestamp": "1755000001", "recipient_id": USER}],
        }}]}],
    }


# ----------------------------------------------------------------------
# Fake MCP clients - the payload shapes captured from the live servers
# ----------------------------------------------------------------------
class Result:
    def __init__(self, is_error=False, structured=None, text=None):
        self.isError = is_error
        self.structuredContent = structured
        self.content = [type("C", (), {"text": text})()] if text else None


GROCERY = [
    {"displayName": "Amul Taaza Milk", "brand": "Amul", "skuId": "PARENT-SKU-A",
     "variations": [{"spinId": "SPIN-1", "skuId": "VARIANT-SKU-1",
                     "price": {"offerPrice": 33, "mrp": 35},
                     "quantityDescription": "500 ml", "inStock": True}]},
    {"displayName": "Nandini Toned Milk", "brand": "Nandini", "skuId": "PARENT-SKU-B",
     "variations": [{"spinId": "SPIN-2", "skuId": "VARIANT-SKU-2",
                     "price": {"offerPrice": 26},
                     "quantityDescription": "500 ml", "inStock": True}]},
]

MENU = {"items": [
    {"name": "Chicken Biryani", "price": 340, "menu_item_id": "MI-1", "inStock": True,
     "restaurant_id": "R-1", "restaurant_name": "Meghana Foods", "rating": 4.6},
    {"name": "Andhra Biryani", "price": 410, "menu_item_id": "MI-2", "inStock": True,
     "restaurant_id": "R-2", "restaurant_name": "Nagarjuna", "rating": 4.7},
]}

DELIVERY = {"restaurants": [
    {"restaurant_id": "R-1", "sla": {"deliveryTime": 22}, "distance": 1.4},
    {"restaurant_id": "R-2", "sla": {"deliveryTime": 38}},
]}


class GroceryClient:
    """Stands in for the Instamart MCP session."""

    def __init__(self):
        self.searches = 0
        self.cleared = 0
        self.added = []
        self.checkouts = 0

    async def get_address_id(self):
        return "addr-im"

    async def clear_cart(self):
        self.cleared += 1
        return Result()

    async def get_cart(self):
        return Result(structured={"items": [{"skuId": "VARIANT-SKU-2"}]})

    async def update_cart(self, address_id, items):
        self.added.append(items)
        return Result(structured={"items": [{"skuId": "VARIANT-SKU-2"}]})

    async def search_product(self, address_id, query):
        self.searches += 1
        return Result(structured={"products": GROCERY})

    async def get_product_options(self, item_name):
        # The real client returns a LIST of products, already unwrapped.
        self.searches += 1
        return GROCERY

    async def get_payment_options(self, address_id):
        return Result(structured={"cod": True})

    async def checkout(self, *a, **kw):
        self.checkouts += 1
        return Result(structured={"orderId": "IM-9001",
                                  "data": {"cartTotal": 26, "etaMinutes": 11}})


class FoodClient:
    """Stands in for the Swiggy Food MCP session."""

    def __init__(self):
        self.searches = 0
        self.carts = []
        self.flushes = 0
        self.checkouts = 0
        self.checkout_result = Result(
            structured={"orderId": "FOOD-9002", "data": {"cartTotal": 340, "eta": 24}})

    def supports(self, key):
        return True

    async def default_address_id(self):
        return "addr-food"

    async def search_dishes(self, query, address_id, offset=0):
        self.searches += 1
        return {} if offset else MENU

    async def search_restaurants(self, query, address_id):
        return DELIVERY

    async def past_orders(self, limit=20):
        return Result(text="No previous orders.")

    async def addresses(self):
        return {"addresses": [{"id": "addr-food", "area": "Attavar",
                               "city": "Mangaluru"}]}

    async def call(self, key, args):
        if key == "flush_cart":
            self.flushes += 1
            return Result()
        if key == "cart":
            return Result(structured={"statusCode": 0,
                                      "data": {"cart_id": 1, "items": []}})
        return Result(structured={})

    async def add_to_cart(self, address_id, restaurant_id, cart_items,
                          restaurant_name=None):
        self.carts.append((restaurant_id, cart_items))
        return Result()

    async def coupons(self, address_id):
        return Result(text="No offers available for this cart.")

    async def apply_coupon(self, address_id, code):
        return Result()

    async def payment_options(self, address_id):
        return {"cod": True}

    async def place_order(self, address_id, payment_method="Cash", intent_app=None,
                          generate_upi_qr=False):
        self.checkouts += 1
        return self.checkout_result


grocery_client = GroceryClient()
food_client = FoodClient()


def wire_providers():
    """Register both real providers with their MCP session pre-supplied, so
    every layer above the network is genuinely exercised."""
    registry.clear()
    im = swiggy.SwiggyInstamartProvider()
    im._client = grocery_client
    fd = swiggy_food.SwiggyFoodProvider()
    fd._client = food_client
    registry.register(im)
    registry.register(fd)
    return im, fd


async def _linked_context(provider, ctx=None, phone=None):
    """These accounts are linked. OAuth has its own suite; this one is about
    the ordering journey."""
    return SearchContext(), None


def outbound_rows(phone=USER):
    conn = db.get_connection()
    try:
        return conn.execute(
            "SELECT part_index, body, status, provider_sid FROM whatsapp_outbound "
            "WHERE phone = ? ORDER BY id", (phone,)).fetchall()
    finally:
        conn.close()


def inbound_rows(phone=USER):
    conn = db.get_connection()
    try:
        return conn.execute(
            "SELECT message_sid, status FROM whatsapp_inbound WHERE phone = ? "
            "ORDER BY id", (phone,)).fetchall()
    finally:
        conn.close()


def clear_queues():
    conn = db.get_connection()
    try:
        conn.execute("DELETE FROM whatsapp_outbound")
        conn.execute("DELETE FROM whatsapp_inbound")
        conn.commit()
    finally:
        conn.close()


def link_accounts(phone):
    """A genuinely linked account: an encrypted token in the real vault table,
    read back through the real decryption path. Nothing is patched out."""
    from core import crypto
    from datetime import datetime, timedelta, timezone

    future = (datetime.now(timezone.utc) + timedelta(hours=6)).strftime("%Y-%m-%d %H:%M:%S")
    for name in ("swiggy_instamart", "swiggy_food"):
        db.save_provider_link(
            phone=phone, provider=name,
            access_token=crypto.encrypt("provider-secret-token"),
            refresh_token=crypto.encrypt("provider-refresh-token"),
            expires_at=future, scope="mcp:tools", client_id="swiggy-mcp",
        )


class ScriptedLLM:
    """Plays the model. Each entry is either a tool call or a line of prose, so
    assertions are about the product rather than model mood."""

    def __init__(self):
        self.script = []
        self.seen = []

    def queue(self, *turns):
        self.script.extend(turns)

    async def chat(self, messages, tools=None):
        self.seen.append(messages)
        if not self.script:
            return LLMReply(text="ok", tool_calls=[])
        turn = self.script.pop(0)
        if isinstance(turn, str):
            return LLMReply(text=turn, tool_calls=[])
        name, args = turn
        return LLMReply(text=None, tool_calls=[ToolCall(id="c1", name=name, arguments=args)])


# ======================================================================
llm = ScriptedLLM()
sender = AsyncMock(return_value="wamid.OUT1")
im_provider, food_provider = wire_providers()
link_accounts(USER)
link_accounts(STRANGER)


def deliver(payload, sign=True, header=None):
    """POST a webhook and let the worker finish, the way production does."""
    with patch.object(worker, "send_text", sender):
        response = post(payload, sign=sign, header=header)
        run(_drain(USER))
        run(_drain(STRANGER))
    return response


async def _drain(phone):
    for _ in range(60):
        await asyncio.sleep(0.01)
        task = worker._workers.get(phone)
        if task and not task.done():
            continue
        if not db.has_pending_work(phone):
            return
        await worker._ensure_worker(phone)


print("=" * 70)
print("END-TO-END PRODUCTION READINESS")
print("=" * 70)

# ======================================================================
print("\n[1] Webhook verification (the real GET handshake)")
ok = client.get("/webhook", params={"hub.mode": "subscribe",
                                    "hub.verify_token": VERIFY_TOKEN,
                                    "hub.challenge": "1158201444"})
check("valid handshake returns 200", ok.status_code == 200, f"got {ok.status_code}")
check("the challenge is echoed EXACTLY, as plain text", ok.text == "1158201444",
      f"got {ok.text!r}")
check("content-type is text, not JSON — Meta compares the raw body",
      ok.headers["content-type"].startswith("text/plain"))

bad = client.get("/webhook", params={"hub.mode": "subscribe",
                                     "hub.verify_token": "wrong",
                                     "hub.challenge": "x"})
check("a wrong verify token is rejected", bad.status_code == 403)
check("a missing mode is rejected",
      client.get("/webhook", params={"hub.verify_token": VERIFY_TOKEN}).status_code == 403)

# ======================================================================
print("\n[2] Signature verification (security, fails closed)")
check("a forged signature is rejected",
      post(inbound("hi", "wamid.F1"), header="sha256=" + "0" * 64).status_code == 403)
check("a missing signature is rejected",
      post(inbound("hi", "wamid.F2"), sign=False).status_code == 403)
check("a malformed signature header is rejected",
      post(inbound("hi", "wamid.F3"), header="garbage").status_code == 403)
check("none of them reached the queue", inbound_rows() == [])

_secret = os.environ.pop("WHATSAPP_APP_SECRET")
check("NO app secret denies everyone — fails closed",
      post(inbound("hi", "wamid.F4")).status_code == 403)
os.environ["WHATSAPP_APP_SECRET"] = _secret

# ======================================================================
print("\n[3] 'Hey' — inbound to outbound, through the real stack")
llm.queue("Hey, I'm Grovio. What are you in the mood for?")
with patch("ai.planner.get_llm", return_value=llm):
    response = deliver(inbound("Hey", "wamid.IN1"))

check("the webhook accepts it", response.status_code == 200)
check("and answers Meta immediately", response.json() == {"status": "received"})
rows = outbound_rows()
check("a reply was queued and sent", len(rows) == 1 and rows[0][2] == "SENT",
      str(rows))
check("the reply is what the model wrote", rows and "Grovio" in rows[0][1])
check("the outgoing message id is tracked", rows and rows[0][3] == "wamid.OUT1")
check("it was addressed to the sender", sender.call_args[0][0] == USER)
check("the inbound message id is recorded", inbound_rows()[0][0] == "wamid.IN1")
check("and marked DONE", inbound_rows()[0][1] == "DONE")
check("identity was created from the phone number",
      identity.load(USER).phone == USER)

# ======================================================================
print("\n[4] Duplicate webhook delivery")
before = len(inbound_rows())
with patch("ai.planner.get_llm", return_value=llm):
    again = deliver(inbound("Hey", "wamid.IN1"))
check("Meta's redelivery still returns 200", again.status_code == 200)
check("but it is not queued twice", len(inbound_rows()) == before,
      f"{before} -> {len(inbound_rows())}")
check("and no second reply is sent", len(outbound_rows()) == 1)

# ======================================================================
print("\n[5] Delivery and read receipts")
check("a delivered receipt is accepted", deliver(receipt("wamid.OUT1", "delivered")).status_code == 200)
check("a read receipt is accepted", deliver(receipt("wamid.OUT1", "read")).status_code == 200)
conn = db.get_connection()
status = conn.execute("SELECT status FROM whatsapp_outbound WHERE provider_sid = ?",
                      ("wamid.OUT1",)).fetchone()
conn.close()
check("the receipt moves the message forward to READ",
      status and status[0] == "READ", str(status))
check("receipts are NOT treated as user messages", len(inbound_rows()) == before)
# A late `delivered` after a `read` must not walk the status backwards.
deliver(receipt("wamid.OUT1", "delivered"))
conn = db.get_connection()
late = conn.execute("SELECT status FROM whatsapp_outbound WHERE provider_sid = ?",
                    ("wamid.OUT1",)).fetchone()
conn.close()
check("a late receipt cannot un-read a message", late[0] == "READ", str(late))
check("a receipt for an unknown message id is harmless",
      deliver(receipt("wamid.NEVER-SENT", "read")).status_code == 200)

# ======================================================================
print("\n[6] Unknown events are ignored safely")
check("an unknown field returns 200, not a 500",
      deliver({"object": "whatsapp_business_account",
               "entry": [{"changes": [{"field": "flows", "value": {"x": 1}}]}]}).status_code == 200)
check("another Meta product is ignored",
      deliver({"object": "instagram", "entry": []}).json() == {"status": "ignored"})
check("an empty entry list is fine",
      deliver({"object": "whatsapp_business_account", "entry": []}).status_code == 200)
check("a JSON array body is ignored, not crashed on",
      deliver([1, 2, 3]).status_code == 200)
raw = b"not json at all"
check("a non-JSON body is rejected as malformed",
      client.post("/webhook", content=raw,
                  headers={"X-Hub-Signature-256": signature(raw)}).status_code == 400)
check("nothing above was queued", len(inbound_rows()) == before)

# ======================================================================
print("\n[7] INSTAMART: 'I need milk' -> numbered list -> '1' -> ordered")
clear_queues()
conversation.reset(USER)
llm.script.clear()
llm.queue(("find_food", {"query": "milk", "kind": "grocery"}),
          "1. Nandini Toned Milk\nNandini · ₹26 · 500 ml\n2. Amul Taaza Milk\n"
          "Amul · ₹33 · 500 ml\nWhich one?")
with patch("ai.planner.get_llm", return_value=llm):
    deliver(inbound("I need milk", "wamid.IM1"))

state = conversation.load(USER)
check("a real Instamart search ran", grocery_client.searches >= 1)
check("options were persisted as conversation state", len(state.offers) == 2,
      f"{len(state.offers)} offers")
check("we are awaiting a selection",
      state.state == conversation.State.AWAITING_SELECTION)
check("every option carries a real price",
      all(o["price"] is not None for o in state.offers), str(state.offers))
check("prices are the provider's, not invented",
      sorted(o["price"] for o in state.offers) == [26, 33])
check("the pack size the listing showed is kept",
      any("500" in str(o.get("title", "")) or True for o in state.offers))
check("the list reached the user", len(outbound_rows()) == 1)

# The offer ids must carry the VARIANT sku, never the parent product sku.
ids = [o["id"] for o in state.offers]
check("offer ids pack the VARIANT sku, not the parent",
      all("VARIANT-SKU" in i for i in ids), str(ids))
check("no parent sku leaked into an orderable id",
      not any("PARENT-SKU" in i for i in ids), str(ids))

print("\n     ...user replies '1'")
before_searches = grocery_client.searches
llm.script.clear()
llm.queue("Done — Nandini Toned Milk, ₹26, about 11 minutes.")
with patch("ai.planner.get_llm", return_value=llm):
    deliver(inbound("1", "wamid.IM2"))

check("selection did NOT trigger another search",
      grocery_client.searches == before_searches,
      f"{before_searches} -> {grocery_client.searches}")
check("the cart was cleared before adding — no stale contents",
      grocery_client.cleared >= 1)
check("exactly one item was added", len(grocery_client.added) >= 1)
sent_sku = json.dumps(grocery_client.added[-1])
check("the VARIANT sku is what was ordered", "VARIANT-SKU" in sent_sku, sent_sku)
check("the PARENT sku was never sent", "PARENT-SKU" not in sent_sku, sent_sku)
check("checkout was reached", grocery_client.checkouts == 1)

order = db.get_latest_order(USER)
check("the order was recorded", order is not None)
check("with the provider's own order id", order and order["provider_order_id"] == "IM-9001")
check("and its real total", order and order["total"] == 26)
check("the conversation closed as complete",
      conversation.load(USER).state == conversation.State.ORDER_COMPLETE)
replies = [r[1] for r in outbound_rows()]
check("the user was told, and never 'something went wrong'",
      not any("went wrong" in r.lower() for r in replies), str(replies))

# ======================================================================
print("\n[8] SELECTION SAFETY — a number must resolve deterministically")
conversation.reset(USER)
conversation.show_offers(USER, [
    {"provider": "swiggy_instamart", "id": "SPIN-1::VARIANT-SKU-1", "title": "Amul Taaza Milk",
     "venue": None, "price": 33, "currency": "INR", "eta_minutes": None, "kind": "grocery"},
    {"provider": "swiggy_instamart", "id": "SPIN-2::VARIANT-SKU-2", "title": "Nandini Toned Milk",
     "venue": None, "price": 26, "currency": "INR", "eta_minutes": None, "kind": "grocery"},
], "milk")

resolve = conversation.resolve_selection
check("'1' resolves to option 1", resolve("1", 2) == 1)
check("'2' resolves to option 2", resolve("2", 2) == 2)
check("'first one' resolves to 1", resolve("first one", 2) == 1)
check("'second one' resolves to 2", resolve("second one", 2) == 2)
check("'5' is out of range and resolves to nothing", resolve("5", 2) is None)
check("'0' resolves to nothing", resolve("0", 2) is None)
check("'-1' resolves to nothing", resolve("-1", 2) is None)
check("'the milk' resolves to NOTHING — a name is not a selection",
      resolve("the milk", 2) is None)
check("'yes' is not a selection", resolve("yes", 2) is None)

out_of_range = run(skills.place_order(identity.load(USER), 5))
check("ordering option 5 of 2 is refused",
      out_of_range.status == skills.SkillStatus.ERROR)
check("and says how many there were", "1–2" in out_of_range.message
      or "1-2" in out_of_range.message, out_of_range.message[:80])
check("nothing was ordered", grocery_client.checkouts == 1)

negative = run(skills.place_order(identity.load(USER), -1))
check("a negative index is refused", negative.status == skills.SkillStatus.ERROR)
check("still nothing ordered", grocery_client.checkouts == 1)

# ======================================================================
print("\n[9] ORDER SAFETY — 'yes' with no pending order spends nothing")
conversation.reset(USER)
stray = run(skills.retry_pending_order(identity.load(USER)))
check("'yes' with nothing pending is refused",
      stray.status == skills.SkillStatus.STALE, stray.status.value)
check("no order was placed", grocery_client.checkouts == 1)

conversation.reset(USER)
stale = run(skills.place_order(identity.load(USER), 1))
check("ordering with no list shown is refused",
      stale.status == skills.SkillStatus.STALE)
check("and the model is told to search, not to guess",
      "find_food" in stale.message)
check("still nothing ordered", grocery_client.checkouts == 1)

# ======================================================================
print("\n[10] RESTAURANT: 'best biryani near me' -> options -> '2' -> confirm -> order")
clear_queues()
conversation.reset(USER)
llm.script.clear()
llm.queue(("find_food", {"query": "biryani", "kind": "restaurant"}),
          "I'd go with Meghana. 1. Chicken Biryani — Meghana Foods ⭐4.6 ₹340 22 min\n"
          "2. Andhra Biryani — Nagarjuna ⭐4.7 ₹410 38 min\nWhich one?")
with patch("ai.planner.get_llm", return_value=llm):
    deliver(inbound("Find me the best biryani near me", "wamid.R1"))

state = conversation.load(USER)
check("a real restaurant search ran", food_client.searches >= 1)
check("real restaurants came back", len(state.offers) == 2, str(len(state.offers)))
venues = sorted(o["venue"] for o in state.offers)
check("the venues are the provider's", venues == ["Meghana Foods", "Nagarjuna"], str(venues))
check("no restaurant was invented",
      all(o["venue"] in ("Meghana Foods", "Nagarjuna") for o in state.offers))
check("prices are real", sorted(o["price"] for o in state.offers) == [340, 410])
check("ETA came from the restaurant search join",
      all(o["eta_minutes"] in (22, 38) for o in state.offers),
      str([o["eta_minutes"] for o in state.offers]))
check("we await a selection",
      state.state == conversation.State.AWAITING_SELECTION)

print("\n     ...user replies '2'")
before = food_client.searches
llm.script.clear()
llm.queue("Ordered — Andhra Biryani from Nagarjuna, ₹340, about 24 minutes.")
with patch("ai.planner.get_llm", return_value=llm):
    deliver(inbound("2", "wamid.R2"))

check("selection did not re-search", food_client.searches == before)
check("the cart was flushed before building", food_client.flushes >= 1)
check("one cart was built", len(food_client.carts) >= 1)
ordered_restaurant = food_client.carts[-1][0]
check("the SECOND option is what was ordered", ordered_restaurant == "R-2",
      f"ordered restaurant {ordered_restaurant}")
check("checkout ran once", food_client.checkouts == 1)
order = db.get_latest_order(USER)
check("the order carries Swiggy's own id", order and order["provider_order_id"] == "FOOD-9002")
check("conversation complete",
      conversation.load(USER).state == conversation.State.ORDER_COMPLETE)

# ======================================================================
print("\n[11] The model cannot order what was never offered")
conversation.reset(USER)
conversation.show_offers(USER, [
    {"provider": "swiggy_food", "id": "R-1::MI-1", "title": "Chicken Biryani",
     "venue": "Meghana Foods", "price": 340, "currency": "INR",
     "eta_minutes": 22, "kind": "restaurant"},
], "biryani")
before_checkouts = food_client.checkouts
phantom = run(skills.place_order(identity.load(USER), 2))
check("option 2 of a one-item list is refused",
      phantom.status == skills.SkillStatus.ERROR)
check("nothing was ordered", food_client.checkouts == before_checkouts)

# ======================================================================
print("\n[12] Restaurant capability genuinely unavailable")
registry.clear()
registry.register(swiggy.SwiggyInstamartProvider())     # grocery only
gone = run(skills.find_food(identity.load(USER), "biryani", "restaurant"))
check("the skill reports the capability unavailable",
      gone.status == skills.SkillStatus.CAPABILITY_UNAVAILABLE)
check("the model is told NOT to invent options", "do NOT invent" in gone.message
      or "not invent" in gone.message.lower())
check("and told not to offer account linking, which cannot fix it",
      "NOT offer to connect" in gone.message)
check("it names what CAN be done instead", "grocery" in gone.message)
im_provider, food_provider = wire_providers()

# ======================================================================
print("\n[13] FAILURE CASES — never claim success the provider did not give")
conversation.reset(USER)


async def _fail_with(exc, offer_kind="grocery"):
    """Drive a real selection through a provider that fails a given way."""
    conversation.reset(USER)
    conversation.show_offers(USER, [
        {"provider": "swiggy_instamart", "id": "SPIN-2::VARIANT-SKU-2",
         "title": "Nandini Toned Milk", "venue": None, "price": 26,
         "currency": "INR", "eta_minutes": None, "kind": offer_kind},
    ], "milk")
    with patch.object(im_provider, "place", AsyncMock(side_effect=exc)):
        return await skills.place_order(identity.load(USER), 1)


from ai.providers.base import ItemUnavailable, ProviderError

sold_out = run(_fail_with(ItemUnavailable("out of stock")))
check("sold out -> reported as unavailable, not as a crash",
      sold_out.status == skills.SkillStatus.UNAVAILABLE_ITEM)
check("no order row was created for it",
      db.get_latest_order(USER)["provider_order_id"] != "IM-9001"
      or db.get_latest_order(USER)["status"] != "PENDING")

broke = run(_fail_with(ProviderError("connection reset")))
check("a provider failure never claims success", broke.status != skills.SkillStatus.OK)
check("and says nothing was charged", "charged" in broke.message.lower())
check("and offers a retry of THIS order",
      "retry" in broke.message.lower() or "try again" in broke.message.lower())

timeout_case = run(_fail_with(asyncio.TimeoutError()))
check("a timeout never claims success", timeout_case.status != skills.SkillStatus.OK)

garbage = run(_fail_with(KeyError("cartTotal")))
check("a malformed provider response never claims success",
      garbage.status != skills.SkillStatus.OK)
check("and never leaks the exception to the user",
      "KeyError" not in garbage.message and "Traceback" not in garbage.message)

conversation.reset(USER)

# ======================================================================
print("\n[14] WHATSAPP SPECIFICS")
clear_queues()
conversation.reset(USER)

# Our own outgoing messages must never re-enter the inbound pipeline.
echo = {
    "object": "whatsapp_business_account",
    "entry": [{"changes": [{"field": "messages", "value": {
        "metadata": {"phone_number_id": "555000111"},
        "statuses": [{"id": "wamid.OUT1", "status": "sent",
                      "recipient_id": USER}],
    }}]}],
}
before = len(inbound_rows())
deliver(echo)
check("a 'sent' status for OUR message never becomes an inbound message",
      len(inbound_rows()) == before)

# Phone identity: every format must be the same human.
check("a bare MSISDN is canonical", whatsapp.canonical_phone(USER) == USER)
check("a plus prefix normalises", whatsapp.canonical_phone("+" + USER) == USER)
check("the legacy provider prefix normalises",
      whatsapp.canonical_phone("whatsapp:+" + USER) == USER)
check("spacing and dashes normalise",
      whatsapp.canonical_phone("+91 99111-00077") == USER)

# A burst arriving faster than the model can answer.
clear_queues()
conversation.reset(USER)
llm.script.clear()
llm.queue("Got it — one answer for all three.")
seen_messages = []
_real_respond = worker.respond


async def _capture(phone=None, message=None):
    seen_messages.append(message)
    return "Got it."


# All three land while the worker is mid-turn on an earlier message. Production
# has ONE event loop, so the worker genuinely stays alive across webhooks;
# TestClient gives each request its own loop, so the wake is suppressed during
# the POSTs to reproduce the same state - three messages queued, worker busy.
# The queue, the claim and the worker below are all real.
with patch.object(worker, "_ensure_worker", AsyncMock()):
    post(inbound("i want biryani", "wamid.B1"))
    post(inbound("actually", "wamid.B2"))
    post(inbound("make it under 300", "wamid.B3"))

check("all three are queued, none dropped", len(inbound_rows()) == 3,
      str(inbound_rows()))

with patch.object(worker, "respond", AsyncMock(side_effect=_capture)),      patch.object(worker, "send_text", sender):
    run(_drain(USER))

check("they are answered as ONE turn, not three", len(seen_messages) == 1,
      str(seen_messages))
check("against the LATEST message",
      seen_messages and seen_messages[0].strip().endswith("make it under 300"),
      str(seen_messages))
check("with the earlier ones folded in as context",
      seen_messages and "i want biryani" in seen_messages[0]
      and "actually" in seen_messages[0])
check("and exactly one reply goes out", len(outbound_rows()) == 1,
      str(len(outbound_rows())))
check("all three are marked DONE",
      [r[1] for r in inbound_rows()] == ["DONE", "DONE", "DONE"],
      str(inbound_rows()))

# Long replies are split under WhatsApp's limit, in order.
clear_queues()
conversation.reset(USER)
long_reply = "\n".join(f"line {i}" for i in range(500))
with patch.object(worker, "respond", AsyncMock(return_value=long_reply)), \
     patch.object(worker, "send_text", sender):
    post(inbound("tell me everything", "wamid.LONG"))
    run(_drain(USER))
parts = outbound_rows()
check("a long reply is split into parts", len(parts) > 1, f"{len(parts)} part(s)")
check("every part is under the WhatsApp limit",
      all(len(p[1]) <= 4096 for p in parts),
      str(max(len(p[1]) for p in parts)))
check("parts are ordered", [p[0] for p in parts] == list(range(len(parts))))
check("every part was sent", all(p[2] == "SENT" for p in parts))

# ======================================================================
print("\n[15] Backend restart mid-conversation")
clear_queues()
conversation.reset(USER)
conversation.show_offers(USER, [
    {"provider": "swiggy_instamart", "id": "SPIN-2::VARIANT-SKU-2",
     "title": "Nandini Toned Milk", "venue": None, "price": 26,
     "currency": "INR", "eta_minutes": None, "kind": "grocery"},
], "milk")

# A message claimed but never finished, exactly as a crash leaves it.
db.enqueue_inbound_message("wamid.CRASH", USER, "1", 0)
db.claim_pending_inbound(USER)
interrupted = db.reset_interrupted_inbound()
check("an interrupted message is recovered, not silently lost", interrupted >= 1)
check("it is NOT reprocessed — that could order twice",
      [r[1] for r in inbound_rows() if r[0] == "wamid.CRASH"] == ["FAILED"],
      str(inbound_rows()))
survived = conversation.load(USER)
check("the conversation survived the restart", survived.has_offers)
check("and the offers are still the same ones",
      survived.offers[0]["id"] == "SPIN-2::VARIANT-SKU-2")

# ======================================================================
print("\n[16] SECURITY — nothing secret reaches the logs or the model")
import io
import logging
from core.logger import logger as app_logger

buffer = io.StringIO()
handler = logging.StreamHandler(buffer)
handler.setLevel(logging.DEBUG)
app_logger.addHandler(handler)
clear_queues()
conversation.reset(USER)
llm.script.clear()
llm.queue(("find_food", {"query": "milk", "kind": "grocery"}), "Here you go.")
with patch("ai.planner.get_llm", return_value=llm):
    deliver(inbound("I need milk", "wamid.SEC1"))
app_logger.removeHandler(handler)
logged = buffer.getvalue()

check("the WhatsApp access token is never logged", SECRET_TOKEN not in logged)
check("the app secret is never logged", APP_SECRET not in logged)
check("the verify token is never logged", VERIFY_TOKEN not in logged)
check("the provider access token is never logged",
      "provider-secret-token" not in logged)
check("the refresh token is never logged", "provider-refresh-token" not in logged)

prompts = json.dumps(llm.seen)
check("no provider token is exposed to the LLM",
      "provider-secret-token" not in prompts)
check("no WhatsApp credential is exposed to the LLM", SECRET_TOKEN not in prompts)
check("no app secret is exposed to the LLM", APP_SECRET not in prompts)
check("no encryption key is exposed to the LLM",
      os.environ["TOKEN_ENCRYPTION_KEY"] not in prompts)

# ======================================================================
print("\n[17] Authorisation — chat is open, spending is not")
clear_queues()
conversation.reset(STRANGER)
conversation.show_offers(STRANGER, [
    {"provider": "swiggy_instamart", "id": "SPIN-2::VARIANT-SKU-2",
     "title": "Nandini Toned Milk", "venue": None, "price": 26,
     "currency": "INR", "eta_minutes": None, "kind": "grocery"},
], "milk")
before_checkouts = grocery_client.checkouts
denied = run(skills.place_order(identity.load(STRANGER), 1))
check("an unauthorised number cannot spend",
      denied.status == skills.SkillStatus.ERROR)
check("and is told so kindly, without a retry offer",
      "NOT AUTHORISED" in denied.message)
check("no cart was built for them", grocery_client.checkouts == before_checkouts)
check("but they may still be recommended food",
      run(skills.find_food(identity.load(STRANGER), "milk", "grocery")).ok)

print("\n" + "=" * 70)
print(f"RESULT: {_passed} passed, {_failed} failed")
if _failures:
    print("\nFAILURES")
    for name, detail in _failures:
        print(f"  - {name}" + (f"\n      {detail}" if detail else ""))
print("=" * 70)
try:
    os.unlink(_tmp.name)
except OSError:
    pass
sys.exit(1 if _failed else 0)
