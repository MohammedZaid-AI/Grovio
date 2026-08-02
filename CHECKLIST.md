# Production Checklist

Work top to bottom. Items marked 🚫 are hard blockers for a public launch — the
rest are things you will wish you had done at 2am.

Current state is marked: ✅ done · ⬜ outstanding.

---

## Blockers

| | Item | Why |
|---|---|---|
| ⬜ 🚫 | **Per-user rate limiting** | Nothing caps message rate today. One number can drive unbounded LLM spend. Token bucket at enqueue (~20/min, 200/day) plus a global daily spend cap. |
| ⬜ 🚫 | **User data deletion** | We hold phone numbers, conversation history, preferences and tokens with no erasure path. DPDP applies from your first real user. |
| ⬜ 🚫 | **Database backups + a tested restore** | A corrupt SQLite file currently loses every user, credential and order. |
| ⬜ 🚫 | **`TOKEN_ENCRYPTION_KEY` in a secret manager, backed up separately** | Not recoverable. Losing it forces every user to reconnect. |
| ⬜ 🚫 | **HTTPS everywhere, `PUBLIC_BASE_URL` set to the live domain** | Providers reject non-https redirects; Meta rejects non-https webhooks. |

---

## Security

| | Item |
|---|---|
| ✅ | OAuth 2.1 + PKCE (S256), endpoints discovered per RFC 8414/9728 |
| ✅ | Tokens and PKCE verifiers encrypted at rest (Fernet) |
| ✅ | Encryption fails closed — no key means no linking, no plaintext fallback |
| ✅ | Single-use, phone-bound, 10-minute OAuth state (replay-proof) |
| ✅ | Webhook signature verification, constant-time, fails closed |
| ✅ | Credentials confined to one module, enforced by test |
| ✅ | No secrets in the repo; `.env` gitignored; `.env.example` documented |
| ✅ | Provider errors never reach users or the model |
| ⬜ | `DEBUG` confirmed **off** in production (it logs message content) |
| ⬜ | Rotate any credential that has ever been pasted into a chat, ticket or screenshot |
| ⬜ | Prompt-injection fencing for stored facts (TECHDEBT H4 — bounded, not urgent) |
| ⬜ | Dependency vulnerability scan in CI (`pip-audit`) |

## Reliability

| | Item |
|---|---|
| ✅ | Durable inbound/outbound queue; nothing lost on restart |
| ✅ | Dedup by message id; each reply part sent once |
| ✅ | Per-phone ordering guaranteed |
| ✅ | Send retries with permanent-vs-transient classification |
| ✅ | LLM timeout 45s with retries (was 600s) |
| ✅ | Provider failures isolated — one platform cannot break a conversation |
| ⬜ | Exactly **one** instance running (two will double-process — see DEPLOYMENT §8) |
| ⬜ | Process supervision with auto-restart (systemd/Docker restart policy) |
| ⬜ | Order idempotency key (a crash between checkout and save leaves an orphan order) |
| ⬜ | Uptime monitor hitting `/health` with alerting |

## Observability

| | Item |
|---|---|
| ✅ | `/health` reports real readiness and 503s when not ready |
| ✅ | Conversation replay possible from the database |
| ✅ | Startup warns loudly on unsafe configuration |
| ⬜ | Structured JSON logs with a correlation id |
| ⬜ | Error aggregation (Sentry or equivalent) |
| ⬜ | Metrics: messages/min, LLM latency + spend, tool outcomes, order success rate, queue depth |
| ⬜ | Alert on: queue backlog, provider error rate, order failure rate, health 503 |

## Data & compliance

| | Item |
|---|---|
| ⬜ | Deletion path implemented and reachable in conversation ("delete my data") |
| ⬜ | Retention window for conversation history (90 days suggested) |
| ⬜ | Privacy policy published — what is stored, why, for how long, how to erase |
| ⬜ | Terms of service |
| ⬜ | Named owner for data-subject requests |
| ✅ | No payment instrument, address book or location data is stored |

## Product

| | Item |
|---|---|
| ✅ | End-to-end journey works: first message → link → recommend → order → track |
| ✅ | Cannot invent a venue, price, rating or ETA |
| ✅ | Cannot order an item no provider returned |
| ✅ | Honest when a capability is unsupported |
| ✅ | User-facing copy reviewed for tone; no internal names or error codes |
| ⬜ | Restaurant provider live (blocked on Swiggy — see PARTNERSHIP.md) |
| ⬜ | Allergy handling reviewed with fresh eyes before public launch — filtering is a backstop, **not a guarantee** (CLAUDE.md) |

## Deployment

| | Item |
|---|---|
| ✅ | Clean clone installs and boots (`pip install` → `uvicorn` → `/health`) |
| ⬜ | Database directory on a persistent volume (Docker: mount `/app/database`) |
| ⬜ | Reverse proxy forwards `X-Forwarded-Proto` and `X-Forwarded-Host` |
| ⬜ | Deploy strategy set to *recreate*, not rolling (single instance) |
| ⬜ | Meta webhook configured and verified |
| ⬜ | Provider callback URL whitelisted |
| ⬜ | CI running all five test suites on push |
| ⬜ | Rollback rehearsed once |

---

## Launch day

1. `curl /health` → `{"ready": true}`
2. Send a message from a **fresh** number; confirm the welcome
3. Complete a real account link end to end
4. Place one real order and let it arrive
5. Confirm `DEBUG` is off and no tokens appear in logs
6. Watch the first hour of logs live
7. Know your rollback command before you need it

## First week

- Read every conversation. All of them. Nothing else tells you this much this early.
- Track: link completion rate, recommendation acceptance, order completion, and where people drop.
- Log every question the assistant answered badly — that list is your roadmap.
- Watch LLM spend per conversation against the value of an order.
