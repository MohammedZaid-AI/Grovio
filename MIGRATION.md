# Migration Plan — Restaurant ERP → AI Food Concierge

**Phase 1 deliverable.** Audit + plan only. No code changed.

---

## 0. Two blockers to resolve before Phase 2

### BLOCKER 1 — We do not have a food-ordering integration. We have a *grocery* one.

`integrations/swiggy/mcp.json` points at `https://mcp.swiggy.com/im`.
`im` = **Instamart** = groceries. The client (`swiggy_mcp.py`, 516 LOC) calls
exactly seven tools:

```
get_addresses  search_products  get_cart  update_cart  clear_cart
get_payment_options  checkout
```

There is **no** restaurant search, no menus, no dishes, no ratings, no delivery
ETA, no order tracking. Those concepts do not exist anywhere in this repo.

The new product's core promise —

> "I recommend Truffles because you usually order grilled chicken on weekdays,
> it fits your ₹350 budget, is rated 4.7, and delivers in 18 minutes."

— requires restaurant identity, ratings, distance, ETA, and menu data. **None of
it is reachable today.** Swiggy publishes no public restaurant-ordering API.

This directly collides with the spec's own rule: **"Never hallucinate."** If the
recommendation engine is built with no real restaurant data source, the LLM will
invent restaurants, ratings, and ETAs. That is the one failure mode the brief
forbids, and it is guaranteed by the current data situation.

Phases 5–7 (recommendation, ordering, tracking) are **blocked** until a food data
source is chosen. See §6.

*Verification worth 10 minutes:* `list_tools.py` (already in the working tree)
dumps the live MCP tool surface. Run it to confirm Instamart exposes nothing
restaurant-shaped. My sandbox can't reach Swiggy; your machine can.

### BLOCKER 2 — OpenWA is a ban-prone, non-Python transport

Two separate problems:

1. **Runtime mismatch.** `@open-wa/wa-automate` is Node/TypeScript driving
   WhatsApp Web in a headless browser. This is a Python repo. Adopting it means
   a Node process + an IPC bridge + a browser session to babysit.
2. **Account risk.** It is unofficial automation of WhatsApp Web. It violates
   WhatsApp's ToS and numbers get banned in practice. For a product where
   **WhatsApp IS the product**, the transport is the whole business — putting it
   on a ban-prone footing is existential, not a detail.

The compliant alternative is the **WhatsApp Cloud API** (Meta official, free
tier, webhook-shaped like the current code, and *not* Twilio — which satisfies
the "no Twilio" requirement).

Your call, not mine. The plan below puts the transport behind a one-file seam so
this decision is reversible either way and does not block Phases 3–4.

---

## 1. What is actually here

239 tracked files, ~21,900 Python LOC + ~4,900 LOC of frontend.

| Area | Files | LOC | Fate |
|---|---|---|---|
| `db.py` (23 tables) | 1 | 2,116 | 21 of 23 tables dropped |
| `backend/` dashboard (routes, HTML, JS, CSS) | 9 | ~4,900 | Delete |
| `ai/agents/` | 27 | 3,140 | Delete 24, 2 are empty stubs |
| `ai/procurement/` | 13 | ~1,200 | Delete |
| `ai/invoice/` (OCR) | 8 | ~900 | Delete |
| `ai/intelligence/` | 11 | 1,858 | Delete ~9, mine 2 for patterns |
| `ai/langgraph/` | 8 | 1,178 | Rebuild (routing survives, agents don't) |
| `ai/conversation/` | 11 | 694 | Keep ~60% |
| `ai/shopping/` + `ai/services/` | 6 | 1,021 | Mine for the provider layer |
| `backend/whatsapp_worker.py` | 1 | 208 | **Keep as-is** |
| `core/` | 6 | 173 | Keep, rework `llm.py` |
| `tests/` | 19 | 3,373 | Keep 4, delete 15 |

Roughly **70% of the codebase is deleted**.

### The genuinely good assets (keep these)

**`backend/whatsapp_worker.py` — the crown jewel.** 208 lines of correct async
delivery: dedup by message id, per-phone ordering, retry with backoff, permanent
-vs-transient error classification, restart recovery, no lost replies. It is
**transport-agnostic except for one import line**. This survives the pivot
untouched and is worth more than everything being deleted.

**`whatsapp_inbound` / `whatsapp_outbound`** tables + helpers — the durable queue
under that worker. Survive as-is.

**`ai/services/swiggy_service.py` parsing discipline** — `_parse_success` /
`_parse_error` handle a hostile upstream that returns `structuredContent=None`,
JSON-in-text, and partial payloads, and never throw. That defensive posture is
the correct template for *any* provider adapter. Keep the patterns, not the
Instamart specifics.

**`ai/shopping/shopping_session.py`** — a real multi-turn state machine with idle
expiry, resume offers, and lifecycle stages. The concierge needs exactly this
shape. Reuse the skeleton.

**`core/`** — `config`, `logger`, `authz`, `formatters` all survive.

### Landmines found

- **`core/llm.py` is sync and single-turn.** It takes `system` + `user` *strings*
  and returns a string. No message array, no history, no `async`. A
  ChatGPT-quality conversation is impossible on this API. Must be reworked in
  Phase 3 — this is the single most important rewrite.
- **CLAUDE.md claims LangChain; the code does not use it.** LLM calls are raw
  `openai` / `groq` SDK. Docs are wrong.
- **Sessions are in-memory** (`SessionManager` = a `dict`). Every restart wipes
  all conversation state. "Memory is the biggest feature" is incompatible with
  this — memory must move to the DB in Phase 4.
- **`ai/agents/recommendation_engine.py` and `explain_agent.py` are 0 bytes.**
  Already stubbed, never written.
- Deleting OCR removes `easyocr`, `opencv`, `pymupdf`, **and `torch`** — roughly
  2 GB of dependencies and the CPU warning in every boot log.

---

## 2. Target architecture

```
WhatsApp transport  (adapter — Cloud API or OpenWA, one file)
        ↓
FastAPI webhook     (enqueue + 200 immediately)
        ↓
whatsapp_worker     (KEPT AS-IS — queue, ordering, retry, recovery)
        ↓
Conversation Engine (multi-turn, async, streaming-capable)
        ↓
Memory Engine       (durable, per-user, learned)
        ↓
Planner Agent       (intent → needs more info? → recommend / order / track)
        ↓
Recommendation Engine (ranked + reasoned, over REAL data only)
        ↓
Provider Layer      (abstract: search / menu / quote / order / track)
        ↓
Swiggy | Zomato | Blinkit | Zepto
```

The provider interface is the load-bearing abstraction:

```python
class FoodProvider(Protocol):
    async def search(self, query, location, filters) -> list[Venue]
    async def menu(self, venue_id) -> list[MenuItem]
    async def quote(self, cart, address) -> Quote      # price + ETA + fees
    async def place(self, cart, address, payment) -> Order
    async def track(self, order_id) -> OrderStatus
```

`Venue` / `MenuItem` / `Quote` / `Order` are provider-neutral. No provider type
ever leaks upward — that is what makes Zomato/Blinkit additive later.

---

## 3. Phase 2 — Deletion

One commit, mechanical, no new behaviour.

**Delete outright:** `ai/procurement/`, `ai/invoice/`, `ai/finance/`,
`ai/reports/`, `ai/scheduler/` (except the generic tick), `ai/memory/`,
`backend/static/`, `backend/pages/`, `backend/chat.py`, 24 of 27 `ai/agents/`,
9 of 11 `ai/intelligence/`, 15 of 19 tests, root scratch scripts
(`add_paneer.py`, `check_paneer.py`, `test_*.py` at root), `downloads/`.

**Gut:** `db.py` 2,116 → ~200 LOC (drop 21 tables, keep the two WhatsApp queue
tables). `backend/routes.py` 1,122 → ~80 LOC (webhook only; dashboard, auth,
upload, inventory, recipe, deduction routes all go).

**Requirements:** drop `easyocr`, `opencv-python`, `pymupdf`, `torch`, `pandas`,
`numpy`, `apscheduler`, `pyjwt`, `twilio`.

Exit criteria: app boots, webhook accepts a message, worker echoes a reply,
`test_whatsapp_async_delivery.py` still passes.

## 4. Phase 3 — Planner, providers, memory, transport ✅ DONE

- `core/llm.py` reworked: async, multi-turn `messages[]`, tool calling. Groq now
  goes through its OpenAI-compatible endpoint, so there is one client and one
  code path (and one fewer dependency).
- `ai/planner.py` orchestrates. The LLM's only levers are three tools
  (`find_food`, `remember`, `remember_food`); it cannot touch a provider or the
  database directly. Its tool choice *is* intent detection.
- `ai/providers/` — `Offer`/`Provider` protocol, capability registry with
  fan-out and per-provider failure isolation. `swiggy.py` is the only file that
  names a platform, enforced by a test.
- `ai/memory.py` + 3 tables — facts, durable history, food memory, keyed by phone.
- `ai/recommendation.py` — deterministic scoring; the LLM phrases reasons it
  cannot invent.
- `whatsapp/cloud_api.py` + `transport.py` — Cloud API with fail-closed
  HMAC verification, and a seam that normalises blocking SDKs to async.

**Deleted as duplicate logic:** `ConversationEngine`, `ai/conversation/*`
(in-memory session/working memory now superseded by durable memory, and a second
chunker competing with the worker's). The "continue" paging mechanism went with
them — the worker already delivers multi-part replies automatically, which is
better UX for chat than making someone type "continue".

Phase 3 absorbed the durable-memory work originally scoped to Phase 4, so Phase
4 is now about memory *depth*: preference extraction, habit detection
(weekday/weekend, gym days), and learning from accept/reject.

## 5. Phase 4 — Memory

Durable, per-user, in SQLite. Two tiers:

- **Structured facts** (allergies, budget, home/office, cuisines, gym days,
  health goals) — written by an extraction pass after each turn, editable by the
  user in conversation, always injected into context.
- **Behavioural history** (orders, accepted/rejected recommendations, times of
  day) — aggregated into preferences rather than replayed raw.

This phase is fully buildable **regardless of the blockers** and is the real moat.

## 6. Phases 5–7 — BLOCKED on the food data source

Cannot start until one of these is chosen:

| Option | Ordering | Data quality | Legal / stability | Ship |
|---|---|---|---|---|
| **A.** Pivot to groceries (Instamart MCP as-is) | Real, works today | Real | Clean | Now |
| **B.** Reverse-engineered Swiggy/Zomato APIs | Real until it breaks | Real | ToS violation, breaks constantly | Days |
| **C.** Real data + deep-link handoff | User taps to confirm in Swiggy | Real (Google Places: ratings, distance, hours) | Clean | ~1 week |
| **D.** Official Swiggy partner API | Real | Real | Clean | Requires a business deal |

**Recommendation: C.** The brief's own framing is *"Our AI solves the decision
first. Then places the order."* The decision layer is the differentiated,
defensible part and is 100% buildable on legitimate data. Order placement is the
blocked part. C ships the entire value proposition minus the final tap, keeps
"never hallucinate" honest, and the provider interface in §2 means real
placement drops in behind `place()` the day access exists — with zero changes
above the provider layer.

Building A or C is real. Building the recommendation engine with no data source
is building a hallucination machine.

---

## 7. Decisions (RESOLVED)

1. **Food data source → C.** Real data (ratings / distance / hours from a
   legitimate provider) + deep-link handoff for the final confirm. Real
   placement lands behind `provider.place()` if/when official access exists.
2. **Transport → WhatsApp Cloud API.** Meta-official, free tier, webhook-shaped
   like the existing code, not Twilio, no ban risk, stays pure Python.
   OpenWA is **not** being used.
3. **Deletion → hard delete.** Git history is the archive.

### Sequencing note

Phase 2 is **pure deletion** and deliberately leaves the Twilio transport in
place for one more phase, so the app stays bootable and
`test_whatsapp_async_delivery.py` stays green. The Cloud API swap happens in
Phase 3 alongside the `core/llm.py` rework — mixing a 10k-line deletion with a
transport migration in one commit would make both unreviewable.

### Judgment call to flag

`integrations/swiggy/swiggy_mcp.py` + `ai/services/swiggy_service.py` are
**kept** though option C does not use them for food ordering. Rationale: they
are a working, tested integration, "Swiggy integration" is on the KEEP list, and
"grocery ordering" is a named future feature — this is the natural seed of a
grocery provider adapter in Phase 5. The ERP-era conversational shopping flow on
top of them (`ai/shopping/`) **is** deleted. Say the word and the MCP client goes
too.
