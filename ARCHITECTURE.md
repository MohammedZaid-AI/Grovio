# Technical Architecture

An AI food concierge that operates entirely inside WhatsApp. A user describes
what they feel like eating; the system resolves the decision, explains its
reasoning from real catalogue data, and places the order through the platform's
official MCP interface.

---

## System

```
 WhatsApp
        │
        ▼
 ┌──────────────────┐
 │ Baileys gateway  │  Node · the ONLY process that knows WhatsApp exists
 └────────┬─────────┘  QR pairing · fromMe filter · @lid → phone · dedup
          │  POST /webhook/inbound  (X-Gateway-Secret, fails closed)
          ▼
 ┌──────────────────┐
 │  FastAPI route   │  authenticate → persist → 200 OK in milliseconds
 └────────┬─────────┘
          ▼
 ┌──────────────────┐
 │ Delivery worker  │  durable queue · one worker per phone · ordered
 │                  │  dedup by message id · retry w/ backoff
 └────────┬─────────┘  restart recovery
          ▼
 ┌──────────────────┐
 │    Concierge     │  turn entry point
 └────────┬─────────┘
          ▼
 ┌──────────────────┐
 │     Planner      │  LLM orchestrates via a fixed tool set
 └────────┬─────────┘  no direct access to providers, DB or credentials
          ▼
 ┌──────────────────┐
 │      Skills      │  capability + "is this user authorised?"
 └────────┬─────────┘
          ├─────────────► Memory          preferences · history · food memory
          ├─────────────► Recommendation  deterministic scoring + reasons
          ▼
 ┌──────────────────┐
 │ Provider registry│  routed by CAPABILITY, never by platform name
 └────────┬─────────┘
          ├─────────────► OAuth 2.1 + PKCE   linking · refresh · revocation
          ├─────────────► Vault              the only decryptor of tokens
          ▼
 ┌──────────────────┐
 │  Provider adapter│  the only code that names a platform
 └────────┬─────────┘
          ▼
   Swiggy MCP  (Instamart today · Food on approval)
```

**Stack:** Python 3.11 · FastAPI · SQLite (Postgres-ready) · MCP client ·
OpenAI-compatible LLM API. Single process today; the queue and schema port to a
multi-worker deployment without redesign.

---

## Design properties

### 1. It cannot fabricate a restaurant

Every venue, rating, price and ETA in a reply originates from a provider
response in that same conversation. When no provider can serve a request, the
capability layer returns `CAPABILITY_UNAVAILABLE` and the model is instructed to
say so plainly.

There is deliberately **no stub or mock restaurant provider** in the codebase.
Restaurant search is switched off until real access exists, because a plausible
invented answer is worse than an honest refusal.

### 2. It cannot order something it was never offered

`place_order` accepts the **index** of an option that a provider actually
returned and that was persisted when shown — never a free-text dish name. A
model that misreads, or a prompt that tries to steer it, cannot conjure an order
for an item that does not exist in a real catalogue response.

### 3. Recommendations are ranked in code, not by the model

Scoring is deterministic over real fields (budget fit, order history, rating,
ETA, distance). The reasons shown to the user are the ones that actually drove
the score; the LLM phrases them but cannot invent them. This makes every
explanation falsifiable.

### 4. Credentials never leave the provider layer

One module decrypts tokens. The planner is told that a connection is *needed* —
never what it is. This is enforced by a test that tokenises the upper layers and
fails the build if they can so much as name a credential.

### 5. Providers are interchangeable

Business logic asks for a *capability* ("who can find restaurant food?"), never
for a platform. Adding a provider is one adapter implementing one protocol;
nothing above the provider layer changes. A test fails the build if a platform
name appears outside its adapter.

---

## Reliability

| Property | Mechanism |
|---|---|
| No lost messages | Inbound persisted before the webhook returns 200 |
| No duplicate replies | Dedup by platform message id; each reply part sent once |
| Ordered delivery | Exactly one worker per phone drains in arrival order |
| Survives restarts | Queue is durable; pending work is re-driven on boot |
| Isolated failures | One failing provider is logged and skipped, never fatal |
| Bounded latency | LLM calls time out in 45s with retries, then apologise |

47 tests cover the delivery pipeline alone.

---

## Security summary

OAuth 2.1 + PKCE (S256), endpoints discovered per RFC 8414/9728. Tokens and PKCE
verifiers encrypted at rest with Fernet; the system **fails closed** without a
key. Single-use, phone-bound, 10-minute authorisation state prevents replay.
Webhook signatures verified with constant-time comparison, failing closed on
missing secrets. Full detail in [SECURITY.md](SECURITY.md).

---

## Verification

296 automated checks, no network access or API keys required:

```bash
PYTHONPATH=. python tests/test_journey.py    # first message → order → tracking
```

Several suites assert *architecture* rather than behaviour — that the planner
cannot name a credential, that no platform name appears above the provider
layer, and that an unsupported capability degrades honestly. If one fails, a
boundary has eroded.

---

## Current state

Complete and running end to end on grocery (Swiggy Instamart MCP). Restaurant
search is implemented up to the provider boundary and remains disabled pending
Swiggy Builders Club production access — see [PARTNERSHIP.md](PARTNERSHIP.md).
