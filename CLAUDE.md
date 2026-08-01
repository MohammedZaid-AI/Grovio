# AI Food Concierge

## What this is

A conversational food concierge that lives entirely in WhatsApp. The user says
what they feel like eating; the assistant decides *what* and *where*, explains
why, and gets the order placed.

**WhatsApp is the entire product.** There is no dashboard, no admin panel, no
web frontend, no React app. If a feature needs a screen, it is the wrong
feature.

The problem being solved is **decision fatigue**, not ordering mechanics. People
bounce between Swiggy, Zomato, Google and Instagram and still don't know what
they want. The AI resolves the decision first; placing the order is the easy
half.

> ⚠️ This repo was a restaurant-inventory ERP until the pivot. If you find code
> or docs referring to inventory, recipes, suppliers, invoices, OCR, purchase
> orders or a dashboard, it is a leftover — delete it. See `MIGRATION.md`.

## Status

| Phase | Scope | State |
|---|---|---|
| 1 | Audit + migration plan (`MIGRATION.md`) | ✅ Done |
| 2 | Delete the ERP | ✅ Done |
| 3 | Planner + providers + memory + Cloud API transport | ✅ Done |
| 4 | Memory depth (learning, extraction, habits) | ⏳ Next |
| 5 | Recommendation engine + a real restaurant provider | Blocked on a data source |
| 6 | Ordering | — |
| 7 | Tracking | — |

## Architecture

```
WhatsApp  →  webhook (verify, enqueue, return 200 in ms)
                 ↓
          whatsapp_worker      durable queue, per-phone ordering, retries
                 ↓
          ai/concierge.py      turn entry point
                 ↓
          ai/planner.py        LLM orchestrates via tools
                 ├── ai/memory.py           user model (retrieval + writes)
                 ├── ai/recommendation.py   deterministic scoring + reasons
                 └── ai/providers/registry  routed by CAPABILITY, not by name
                                   ↓
                     Swiggy | Zomato | Blinkit | Zepto
```

**Nothing above the provider layer may know which provider it is talking to.**
`tests/test_concierge.py` §10 enforces this mechanically — it fails the build if
a platform name appears in `ai/*.py`, `backend/` or `core/`.

The LLM cannot reach a provider, the database, or a platform directly. Its only
levers are the tools in `planner.TOOLS`. That is what makes the layering real
rather than decorative.

## Layout

```
backend/
  app.py                FastAPI app + lifespan (schema, providers, recovery)
  routes.py             ONE capability: GET/POST /webhook
  whatsapp_worker.py    async delivery worker — do not casually modify
ai/
  concierge.py          turn entry point (run planner, persist, stay friendly)
  planner.py            orchestration: intent → memory → recommend → execute
  memory.py             user model: facts, history, food memory
  recommendation.py     scoring; reasons are derived, never invented
  providers/
    base.py             Offer + Provider protocol (neutral currency)
    registry.py         capability routing, fan-out, failure isolation
    swiggy.py           the ONLY file allowed to know Swiggy exists
core/                   llm (async, tool-calling), config, logger, authz
whatsapp/
  transport.py          seam: picks the transport, normalises it to async
  cloud_api.py          WhatsApp Cloud API (default)
  twilio.py             legacy, WHATSAPP_TRANSPORT=twilio
db.py                   delivery queue + user model
```

## Local development

```bash
python -m venv venv && source venv/Scripts/activate
pip install -r requirements.txt

uvicorn backend.app:app --reload --port 8000     # from repo root

PYTHONPATH=. python tests/test_whatsapp_async_delivery.py
```

`uvicorn --reload` does **not** watch `.env` — restart fully after changing it.

## Environment

| Variable | Purpose |
|---|---|
| `GROQ_API_KEY` / `OPENAI_API_KEY` | LLM inference |
| `OPENAI_BASE_URL`, `LLM_MODEL` | Provider + model override |
| `AUTHORIZED_PHONES` | Allowlist for spending money. **Fails closed.** |
| `WHATSAPP_ACCESS_TOKEN` | Cloud API system-user token |
| `WHATSAPP_PHONE_NUMBER_ID` | Cloud API sender id |
| `WHATSAPP_APP_SECRET` | Webhook signature verification. **Fails closed.** |
| `WHATSAPP_VERIFY_TOKEN` | Echoed during webhook registration |
| `WHATSAPP_TRANSPORT` | `cloud` (default) or `twilio` |
| `DEBUG` | Gate content logging (default off) |

## Rules

1. **Never hallucinate a recommendation.** Restaurant names, ratings, ETAs and
   prices come from a real provider response or they don't get said. This is the
   product's core integrity constraint, not a style preference.
2. **Always explain why.** A recommendation without a reason is a coin flip.
3. **No command syntax.** The user types like they'd text a friend. Ask a
   follow-up only when a genuinely required detail is missing.
4. **Never hardcode provider logic** above the provider layer.
5. **Fail closed** on authorization — a missing allowlist denies everyone.
6. **Never expose raw exceptions, provider errors, or report IDs** to the user.
7. **No dead code**, no compatibility shims, no abstraction with one caller.

## Landmines

- **There is no restaurant provider.** Only grocery (Instamart) is registered,
  so `find_food(kind="restaurant")` returns `CAPABILITY_UNAVAILABLE` by design
  and the concierge says it cannot search yet. Registering a stub that returns
  invented venues would be the single worst change anyone could make here. See
  `MIGRATION.md` §0.
- **Allergy filtering is a backstop, not a guarantee.** `recommendation._is_avoided`
  matches literal names ("peanuts" → "Peanut Salad") but cannot know lobster IS
  shellfish. The avoid list also goes into the system prompt as MUST AVOID.
  Airtight filtering needs allergen tags from the provider.
- `backend/whatsapp_worker.py` is the most carefully-built file here (dedup,
  ordering, retry classification, restart recovery). 47 tests cover it. Change
  it deliberately.
- Scoring is deliberately **deterministic** so explanations cannot be
  rationalised after the fact. Do not move ranking into the LLM.
