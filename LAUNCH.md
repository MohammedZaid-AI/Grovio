# Launch Readiness

Written for someone deciding whether this is real: an investor, a YC partner, a
Swiggy engineer, or a design partner about to hand it their phone number.

**Position in one paragraph.** The product is architecturally complete and runs
end to end today — a stranger can message it, connect a delivery account, get
recommendations built from real provider data, order, and ask where it is. The
recommendation and ordering layers are provider-agnostic and enforced as such by
tests. The one thing missing is *restaurants*, and that is not an engineering
problem: Swiggy's Food MCP exists and does everything required, but third-party
production access is gated behind their Builders Club review. Grocery works now
because we already have that access.

---

## What is finished

| Capability | State | Evidence |
|---|---|---|
| WhatsApp transport | Cloud API (Meta official), fail-closed HMAC verification | 29 tests |
| Message delivery | Durable queue, per-phone ordering, dedup, retry classification, restart recovery | 47 tests |
| Conversation | Async multi-turn, tool-calling, personality, time/day awareness | 68 tests |
| User identity | Phone-as-identity, no signup, onboarding lifecycle | 84 tests |
| Account linking | OAuth 2.1 + PKCE, standards-based discovery, encrypted tokens, refresh, revocation, reconnect | 84 tests |
| Conversation resumption | Original request auto-answered after linking | tested end to end |
| Memory | Preferences, durable history, food memory, learned not asked | tested |
| Recommendations | Deterministic scoring; reasons derived from real fields | tested |
| Ordering | Place by index into real offers; COD | tested |
| Tracking / cancellation | Capability-gated per provider, honest when unsupported | tested |
| Grocery provider | Swiggy Instamart — real search and real orders | live, used |
| Failure handling | Every failure mode returns an honest message; no internals leak | tested |

**295 automated checks**, no network and no API keys required to run them.
Several assert *architecture* rather than behaviour — they tokenise the upper
layers and fail the build if the planner can name a credential or a platform.

### Three properties worth verifying yourself

1. **It cannot invent a restaurant.** With no restaurant provider registered,
   `find_food(kind="restaurant")` returns `CAPABILITY_UNAVAILABLE` and instructs
   the model to say so. There is deliberately no stub provider.
2. **It cannot order something it never offered.** `place_order` takes an index
   into the offers actually returned by a provider and persisted — not a name.
3. **The planner cannot touch a credential.** Enforced by a test, not a convention.

---

## What is blocked, and on whom

### Depends on Swiggy

| # | Item | Why it blocks | Status |
|---|---|---|---|
| S1 | **Builders Club production access** | Their manifest states third-party app development is not permitted pending security review. Without approval we cannot serve a second user. | Not yet applied |
| S2 | **Whitelisting our OAuth callback** | Only Claude/ChatGPT/VS Code/Cursor/Postman/localhost redirect URIs are whitelisted. A stranger cannot complete OAuth against our service until `{PUBLIC_BASE_URL}/link/{provider}/callback` is added. | Blocked on S1 |
| S3 | **`/food` MCP tool names** | Restaurant search/order/track adapter cannot be written against unverified tool names, and guessing would violate the project's core rule. | Verifiable in 10 minutes — see below |
| S4 | Rate limits, and whether ordering stays COD-only | Capacity planning and payment UX | Undocumented publicly |

**S3 is not really blocked.** Local prototyping is explicitly permitted, and
`http://localhost` is a whitelisted redirect. Running

```bash
PYTHONPATH=. python integrations/swiggy/inspect_tools.py   # point it at /food
```

captures the real tool names today. The adapter is then a day's work, because
everything above the provider layer is finished.

### Depends on us

Ordered by what I would actually do first.

| # | Item | Effort | Blocks launch |
|---|---|---|---|
| U1 | **Apply to Builders Club** (`builders@swiggy.in`) — describe the concierge, request production access *and* callback whitelisting | 1 hour | **Yes** — everything queues behind it |
| U2 | **Verify `/food` tool names** and write the restaurant adapter | 1–2 days | **Yes** — it's the product |
| U3 | **Rate limiting** — per-phone token bucket + global spend cap | ~half a day | **Yes** (TECHDEBT H1) |
| U4 | **Data deletion** — "forget me" across all tables, DPDP/GDPR | ~half a day | **Yes** (TECHDEBT H2) |
| U5 | **Database backups** + restore drill | ~half a day | **Yes** — SQLite corruption currently loses everything |
| U6 | Structured logs + correlation id + error aggregation | 1 day | No, but the first incident is diagnosed blind |
| U7 | History retention window | 2 hours | No — but pairs with U4 |
| U8 | Deployment: pin a host, TLS, process supervision, health checks wired to the load balancer | 1 day | Yes, mechanically |
| U9 | CI running the five suites on push | 1 hour | No |
| U10 | Order idempotency key | 1 day | No — matters once volume does |

**Realistic critical path to a public pilot: about a week of our work, plus
Swiggy's review clock.** U3–U5 and U8 are the genuine launch gates on our side;
none of them touch the architecture.

---

## Deployment

Runs as a single process today:

```bash
uvicorn backend.app:app --host 0.0.0.0 --port 8000
```

Required in production:

| Setting | Why |
|---|---|
| `TOKEN_ENCRYPTION_KEY` | Without it, no account can be linked (fails closed by design) |
| `PUBLIC_BASE_URL` (https) | OAuth callbacks; providers reject non-https redirects |
| `WHATSAPP_APP_SECRET` | Webhook signature verification (fails closed) |
| `GROQ_API_KEY` or `OPENAI_API_KEY` | Inference |

The process warns loudly at startup for each of these when misconfigured, and
`GET /health` returns 503 rather than 200 until the database and encryption are
both actually ready — so a load balancer will not route traffic to a process
that cannot serve it.

**Known deployment constraints (see TECHDEBT):** single process only, so no
zero-downtime deploy and no horizontal scaling yet. Restart is safe —
`recover_pending()` re-drives anything queued — but it is a brief gap. Fine for
a pilot; Postgres + Redis is the documented path beyond ~10k users, and the
queue abstraction means it is a driver swap rather than a redesign.

---

## Honest risk register

Things I would want asked in diligence, stated before they are.

| Risk | Assessment |
|---|---|
| **Single-vendor dependency on Swiggy** | Real and material. Mitigated architecturally — the provider layer routes by capability, and ONDC is a documented second provider (FEASIBILITY.md §B) requiring no changes above the provider layer. Not mitigated commercially. |
| **Approval could be refused or slow** | The likeliest bad outcome. Fallback is the deep-link architecture (FEASIBILITY.md Option C): real discovery data plus a hand-off, shipping the decision layer without order placement. |
| **LLM cost per conversation** | ~2 calls per turn, uncapped today (U3). Economics need measuring before scale, not after. |
| **Unverified `/food` surface** | Everything about the restaurant adapter is projected from Swiggy's capability description, not a tool list. Ten minutes of verification removes this. |
| **Prompt injection via stored preferences** | Bounded to one user; money path independently protected by index-based ordering. Partially mitigated, fully documented (TECHDEBT H4). |
| **Regulatory (DPDP)** | Deletion is missing (U4). Small fix, real obligation. |
| **Allergy filtering is not a safety guarantee** | Documented plainly in CLAUDE.md. Name matching catches "peanuts" → "Peanut Salad" but cannot know lobster is shellfish; the constraint is also passed to the model. Airtight filtering needs allergen tags from a provider. **This is the one place where a quiet failure could hurt someone**, and it is deliberately not overstated anywhere in the product. |

---

## What I would not change

For a reviewer wondering what is load-bearing:

- **Deterministic ranking.** Moving scoring into the LLM would make every
  explanation unfalsifiable. It is deliberately in code.
- **Index-based ordering.** The model picks a number from a list a provider
  actually returned. Accepting a dish name would reintroduce hallucinated spend.
- **No stub restaurant provider.** Returning plausible venues while unblocked
  would demo beautifully and be the worst decision available.
- **Fail-closed everywhere.** Missing encryption key, missing webhook secret,
  missing allowlist — all deny rather than degrade.
- **`backend/whatsapp_worker.py`.** 47 tests cover dedup, ordering, retry
  classification and restart recovery. Change it deliberately.
