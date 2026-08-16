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
| 3.5 | Product feasibility audit (`FEASIBILITY.md`) | ✅ Done |
| 4 | Identity + provider account linking (`IDENTITY.md`) | ✅ Done |
| 5 | End-to-end journey: recommend → order → track | ✅ Done |
| 6 | Restaurant provider (`/food` adapter) | Blocked on Builders Club access |

## Architecture

```
WhatsApp  →  webhook (verify, enqueue, return 200 in ms)
                 ↓
          whatsapp_worker      durable queue, per-phone ordering, retries
                 ↓
          ai/concierge.py      turn entry point
                 ↓
          ai/planner.py        LLM orchestrates via tools
                 ↓             (knows nothing about OAuth or platforms)
          ai/skills.py         capability + "can this user do it?"
                 ├── ai/memory.py           user model (retrieval + writes)
                 ├── ai/recommendation.py   deterministic scoring + reasons
                 └── ai/providers/registry  routed by CAPABILITY, not by name
                          └── oauth.py + vault.py   linking, refresh, encryption
                                   ↓
                  Swiggy Instamart (grocery) · Swiggy Food (restaurant)
```

**Swiggy is the only platform implemented.** Zomato runs an MCP server of the
same shape, but third-party apps are not permitted on it yet; Blinkit and Zepto
expose nothing. The registry is genuinely provider-agnostic — a second platform
is one file in `ai/providers/` and a line in `setup()`, with nothing above the
provider layer changing — but there is currently no second platform to add.
That single-supplier dependency is the product's largest structural risk, and
naming it here is more useful than a diagram implying otherwise.

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
  planner.py            orchestration: intent → memory → skills
  skills.py             capability layer; converts provider reality into
                        instructions. Returns NEEDS_LINK, never a token.
  identity.py           user lifecycle, onboarding status
  memory.py             user model: facts, history, food memory
  recommendation.py     scoring; reasons are derived, never invented
  providers/
    base.py             Offer + Provider/LinkableProvider protocols
    registry.py         capability routing, fan-out, failure isolation
    oauth.py            generic OAuth 2.1 + PKCE; discovery per RFC 8414/9728
    vault.py            the ONLY reader of decrypted tokens
    swiggy.py           the ONLY file allowed to know Swiggy exists
core/                   llm (async, tool-calling), crypto, config, logger, authz
whatsapp/
  __init__.py           the seam — import send_text/mark_read from here;
                        selects the transport via WHATSAPP_PROVIDER
  cloud_api.py          the ONLY module that knows Meta exists (production)
  local_client.py       personal WhatsApp Web session (DEVELOPMENT ONLY)
db.py                   delivery queue + user model
```

## Local development

```powershell
python -m venv venv
venv\Scripts\Activate.ps1          # PowerShell. Bash: source venv/Scripts/activate
pip install -r requirements.txt

uvicorn backend.app:app --reload --port 8000     # from repo root

python tests\test_whatsapp_async_delivery.py     # no PYTHONPATH needed
```

**Activate the venv first, or use its interpreter directly.** There are several
Pythons on a typical Windows box and a bare `uvicorn`/`python` usually resolves
to the global one, which has none of these dependencies — the symptom is
`ModuleNotFoundError: No module named 'mcp_use'` at startup, or two test suites
failing to import a dependency. Without activating:

```powershell
venv\Scripts\python.exe -m uvicorn backend.app:app --reload --port 8000
venv\Scripts\python.exe tests\test_ordering_flow.py
```

`uvicorn --reload` does **not** watch `.env` — restart fully after changing it.

### Running the model locally

Ollama speaks the OpenAI protocol, so this is configuration only — no code
path differs:

```
OPENAI_API_KEY=ollama
OPENAI_BASE_URL=http://localhost:11434/v1
LLM_MODEL=gemma4:e4b
```

Verify before trusting it — the whole concierge hangs off tool calling, and a
model that chats fluently but never calls `find_food` fails silently because
the reply still looks like a reply:

```powershell
venv\Scripts\python.exe scripts\check_llm.py
```

Set **`OLLAMA_KEEP_ALIVE=30m`**. Ollama unloads an idle model after 5 minutes
and reloading `gemma4:e4b` costs ~50s on a 6 GB card — the difference between a
3-second reply and a user assuming it is broken.

### Testing without a Meta account

Meta requires an approved business account and a public webhook. To exercise the
concierge before that exists, `WHATSAPP_PROVIDER=local` links a **personal**
WhatsApp number over WhatsApp Web instead:

```powershell
venv\Scripts\python.exe -m pip install neonize "qrcode[pil]"
$env:WHATSAPP_PROVIDER="local"
venv\Scripts\python.exe -m uvicorn backend.app:app --reload --port 8000
```

A QR prints in the terminal (and lands in `.data/whatsapp-session/qr.png`).
Scan it once: **WhatsApp → Settings → Linked devices → Link a device**. The
session persists in `.data/whatsapp-session/` — gitignored — and survives
restarts until you unlink from the phone. Then message that number.

Switch back with `WHATSAPP_PROVIDER=cloud` (or just unset it — cloud is the
default, so production is never opt-in).

⚠️ **Development only.** This is an unofficial client: WhatsApp can ban the
number, so use a spare one, never a number tied to a business account. There is
also no webhook, so no `X-Hub-Signature-256` to verify — the transport is
trusted, which is exactly why it must not ship. **A linked device receives
every message that number gets**, so anyone who texts the number will be
answered by the concierge. They cannot spend anything —
`AUTHORIZED_PHONES` gates ordering — but they will get replies.

`--reload` restarts the WhatsApp socket on every code change. For a long
session, run without it.

## Environment

| Variable | Purpose |
|---|---|
| `GROQ_API_KEY` / `OPENAI_API_KEY` | LLM inference. `OPENAI_*` wins when set |
| `OPENAI_BASE_URL`, `LLM_MODEL` | Provider + model override |
| | Local: `OPENAI_API_KEY=ollama`, `OPENAI_BASE_URL=http://localhost:11434/v1` |
| `AUTHORIZED_PHONES` | Who may ORDER. Chat is open to all. **Fails closed.** |
| `WHATSAPP_PROVIDER` | `cloud` (production, default) or `local` (dev) |
| `WHATSAPP_ACCESS_TOKEN` | Cloud API system-user token |
| `WHATSAPP_PHONE_NUMBER_ID` | Cloud API sender id |
| `WHATSAPP_APP_SECRET` | Webhook signature verification. **Fails closed.** |
| `WHATSAPP_VERIFY_TOKEN` | Echoed during webhook registration. **Fails closed.** |
| `META_API_VERSION` | Graph API version (default in `whatsapp/cloud_api.py`) |
| `TOKEN_ENCRYPTION_KEY` | Fernet key for provider tokens. **Fails closed.** |
| `PUBLIC_BASE_URL` | Base for OAuth callbacks; must be https in production |
| `SWIGGY_OAUTH_CLIENT_ID` | Optional — Swiggy issues client ids by dynamic registration |
| `SWIGGY_OAUTH_AUTHORIZE_URL` / `_TOKEN_URL` | Pin OAuth endpoints; beats discovery. See `OAUTH.md` |
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
   Authorization guards **spending, not conversation**: anyone may chat and be
   recommended food; only `AUTHORIZED_PHONES` may place an order. The gate lives
   at `skills._execute_pending`, the one point every order (first attempt and
   retry) passes through.
6. **Never expose raw exceptions, provider errors, or report IDs** to the user.
7. **No dead code**, no compatibility shims, no abstraction with one caller.

## Landmines

- **There is no restaurant provider yet.** Swiggy's `/food` MCP server exists
  and does everything we need, but third-party production access is gated on
  Builders Club approval (`FEASIBILITY.md`). Until then `find_food(kind=
  "restaurant")` returns `CAPABILITY_UNAVAILABLE` by design. Registering a stub
  that returns invented venues would be the single worst change anyone could
  make here.
- **Never report a failure as an outage unless it is one.** Every exception used
  to become "the provider is temporarily unavailable", so a wrong OAuth URL —
  our bug, unfixable by waiting — told the user Swiggy was down. Classify via
  `ai/providers/failures.py`; only `UNAVAILABLE` may say "try again shortly".
- **OAuth metadata may live at the ORIGIN ROOT**, not under the server path.
  `_metadata_urls` probes the RFC 8414 §3.1 form, the appended form and the
  root. Swiggy serves only the root. See `OAUTH.md`.
- **The transport is a seam, not a fork.** Both transports expose the same
  `send_text`/`classify_send_error` and hand inbound messages to
  `whatsapp_worker.enqueue_and_wake` in the same shape. Nothing above
  `whatsapp/` may learn which one is running — `tests/test_local_transport.py`
  §10 tokenises the local client and fails the build if it names the AI layer.
- **Phone numbers are canonical MSISDNs** — `917795871481`, never
  `whatsapp:+91…`. `whatsapp.canonical_phone` normalises at ingress and
  `db._migrate_phone_keys` rewrites older rows on startup. A second format
  means the same human becomes a second user with no memory or order history.
- **The transport is a seam, not a fork.** Both transports expose the same
  `send_text`/`classify_send_error` and hand inbound messages to
  `whatsapp_worker.enqueue_and_wake` in the same shape. Nothing above
  `whatsapp/` may learn which one is running — `tests/test_local_transport.py`
  §10 tokenises the local client and fails the build if it names the AI layer.
- **Tokens never leave the vault.** `ai/providers/vault.py` is the only module
  that decrypts a credential. `tests/test_identity.py` §10 tokenises
  `planner.py`/`concierge.py`/`skills.py` and fails the build if they can so
  much as name one.
- **`place_order` takes an INDEX, never a name.** The model can only order from
  the list actually shown (persisted in `offer_sessions`). This is the
  money-spending equivalent of not inventing a restaurant — do not "improve" it
  into accepting a dish name.
- **Providers declare `supports_tracking` / `supports_cancellation`.** When
  false, the concierge says so honestly. Never paper over it with a plausible
  status; a fabricated ETA is worse than no ETA.
- **`TOKEN_ENCRYPTION_KEY` fails closed.** No key means provider tokens cannot
  be stored at all. There is deliberately no plaintext fallback — do not add one.
- **Allergy filtering is a backstop, not a guarantee.** `recommendation._is_avoided`
  matches literal names ("peanuts" → "Peanut Salad") but cannot know lobster IS
  shellfish. The avoid list also goes into the system prompt as MUST AVOID.
  Airtight filtering needs allergen tags from the provider.
- `backend/whatsapp_worker.py` is the most carefully-built file here (dedup,
  ordering, retry classification, restart recovery). 47 tests cover it. Change
  it deliberately.
- Scoring is deliberately **deterministic** so explanations cannot be
  rationalised after the fact. Do not move ranking into the LLM.
