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
| 3 | Conversation engine + WhatsApp Cloud API transport | ⏳ Next |
| 4 | Memory | — |
| 5 | Recommendation engine | — |
| 6 | Ordering | — |
| 7 | Tracking | — |

## Architecture

```
WhatsApp  →  webhook (enqueue, return 200 in ms)
                 ↓
          whatsapp_worker         durable queue, per-phone ordering, retries
                 ↓
          ConversationEngine      history, chunking, "continue"
                 ↓
          ai/concierge.py         ← the product seam (Phase 3 fills this)
                 ↓
          Memory → Planner → Recommendations → Provider Layer
                                                    ↓
                                   Swiggy | Zomato | Blinkit | Zepto
```

**Nothing above the provider layer may know which provider it is talking to.**
That boundary is what makes new platforms additive instead of invasive.

## Layout

```
backend/
  app.py                FastAPI app + lifespan (schema, restart recovery)
  routes.py             ONE route: POST /webhook
  whatsapp_worker.py    async delivery worker — do not casually modify
  conversation_engine.py
ai/
  concierge.py          conversation entry point (Phase 3)
  conversation/         session, chunker, working memory
core/                   llm, config, logger, authz, formatters
integrations/swiggy/    Instamart MCP client (grocery; seed of a future provider)
db.py                   delivery queue only — 2 tables
tests/
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
| `TWILIO_ACCOUNT_SID` / `TWILIO_AUTH_TOKEN` / `TWILIO_WHATSAPP_FROM` | Transport (until Phase 3) |
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

- `core/llm.py` is **sync and single-turn** (`system` + `user` strings). It
  cannot support a real conversation — Phase 3 must rework it to async
  multi-turn messages.
- `ai/conversation/session*.py` are **in-memory**; every restart wipes them.
  Durable memory is Phase 4, and memory is the product's moat.
- `backend/whatsapp_worker.py` is the most carefully-built file here (dedup,
  ordering, retry classification, restart recovery). 47 tests cover it. Change
  it deliberately.
- The Swiggy MCP is **Instamart — groceries**. It has no restaurants, menus,
  ratings or ETAs. See `MIGRATION.md` §0 before building anything on it.
