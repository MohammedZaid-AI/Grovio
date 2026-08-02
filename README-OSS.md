# Concierge Kit

> **This is the README for the open-sourceable subset of this repository.** It
> describes everything *except* the provider adapters — i.e. the framework, not
> our Swiggy integration or our product copy. Use it as the README when
> extracting those modules into a public repo. See §"Extracting" at the end.

---

# Concierge Kit

Build a conversational agent that lives in WhatsApp, remembers its users, and
takes real actions on their behalf through third-party platforms — without
letting the model invent facts or touch credentials.

Extracted from a production AI food concierge. The domain is gone; the hard
parts are what remain.

```python
# your whole integration
class MyProvider:
    name = "acme"
    kind = ProviderKind.GROCERY
    oauth = OAuthConfig(server_url="https://acme.example/mcp")

    async def search(self, query, ctx):
        return [Offer(provider=self.name, kind=self.kind,
                      id="1", title="Milk", price=50)]

registry.register(MyProvider())
```

Account linking, token encryption, refresh, conversation resumption, ranking and
delivery are then handled for you.

---

## Why this exists

Most "AI agent" projects fall down in the same four places. This kit is the set
of answers we arrived at after hitting each one.

**1. The model invents things.** Ratings, prices, availability, order
confirmations. Here, facts can only originate from a provider response, and a
capability with no provider returns an explicit *unavailable* signal rather than
letting the model improvise. Ranking is deterministic in code, so the reasons
shown to a user are the ones that actually drove the decision — the model
phrases them and cannot fabricate them.

**2. Credentials leak upward.** A token gets passed "just this once" into
business logic and the boundary is gone. Here one module decrypts tokens, and a
test tokenises the upper layers and fails the build if they can even name one.

**3. Messages get lost.** Chat platforms discard slow webhook replies. Here the
webhook persists and returns 200 in milliseconds; a durable per-user queue does
the slow work, with dedup, ordering, retry classification and restart recovery.

**4. OAuth in a chat window is awkward.** The user is not in a browser and may
authorise ten minutes later, or after your process restarts. Here the flow is
durable in the database, and the user's original request is answered
automatically once they return — they never repeat themselves.

---

## What's included

| Module | Provides |
|---|---|
| `transport/` | WhatsApp Cloud API + Twilio behind one seam; HMAC verification |
| `worker/` | Durable queue, per-user ordering, dedup, retry, restart recovery |
| `planner/` | Async multi-turn LLM orchestration over a fixed tool set |
| `skills/` | Capability layer: "can this user do this, and what happened?" |
| `memory/` | Preferences, durable history, per-user behavioural memory |
| `providers/` | Protocol, capability registry, OAuth 2.1 + PKCE, encrypted vault |
| `recommendation/` | Deterministic scoring with derived (not generated) reasons |

**Not included:** provider adapters, product copy, and anything specific to food.

---

## Quick start

```bash
pip install -r requirements.txt
cp .env.example .env      # set an LLM key and TOKEN_ENCRYPTION_KEY
uvicorn app:app --reload
curl localhost:8000/health
```

### Add a capability

Skills are the layer your agent's tools call. A skill returns a result, never a
credential:

```python
async def find_thing(user, query) -> SkillResult:
    if not registry.supports(ProviderKind.GROCERY):
        return SkillResult(SkillStatus.CAPABILITY_UNAVAILABLE,
                           "No provider can serve this. Say so; do not invent.")

    gaps = await registry.link_gaps(user.phone, ProviderKind.GROCERY)
    if gaps:
        return await link_prompt(user, gaps[0], pending_message=query)

    offers = await registry.search(ProviderKind.GROCERY, query, phone=user.phone)
    return SkillResult(SkillStatus.OK, format_for_model(offers))
```

The planner sees only `SkillResult.message` — an instruction about reality. It
never learns that OAuth exists.

---

## Design rules

Four rules do most of the work. Break them and the guarantees go with them.

1. **Nothing above the provider layer names a platform.** Route by *capability*.
   Enforced by a test.
2. **The model's only levers are its tools.** No database, no HTTP, no
   filesystem, no credentials.
3. **Actions with consequences take an identifier, never free text.** Ordering
   accepts the *index* of an option a provider actually returned. A confused or
   manipulated model cannot conjure an action against something that does not
   exist.
4. **Everything fails closed.** Missing encryption key, missing webhook secret,
   missing provider — deny, never degrade.

---

## Security

- OAuth 2.1 + PKCE (S256); endpoints discovered per RFC 8414/9728 — no
  hardcoded provider URLs
- Tokens and PKCE verifiers encrypted at rest (Fernet); no plaintext fallback
- Single-use, user-bound, short-TTL authorisation state — replay-proof via an
  atomic database claim
- Constant-time webhook signature verification
- Per-user isolation on every table; no shared mutable per-user state

Known gaps in the extracted kit: no rate limiting and no data-deletion helper.
Both are deliberate — they belong to your product's policy, not the framework —
but you must implement them before serving real users.

---

## Testing

No framework. Each suite is a script that exits non-zero on failure and needs no
network or API keys.

```bash
PYTHONPATH=. python tests/test_delivery.py
PYTHONPATH=. python tests/test_identity.py
```

Some suites assert **architecture** rather than behaviour — that credentials
cannot reach the planner, that no platform name appears above the provider
layer. Those are the ones worth keeping green.

---

## Status and limits

Runs as a single process with SQLite. Honest ceilings:

- Comfortable to ~1,000 users
- First break at ~10,000: in-process worker registry and SQLite write contention
- Path beyond: Postgres + Redis; the queue lives behind helper functions, so it
  is a driver swap rather than a redesign

## Licence

MIT.

---

## Extracting from the parent repository

For whoever performs the extraction:

**Include:** `backend/whatsapp_worker.py`, `backend/routes.py`,
`backend/linking.py`, `whatsapp/`, `ai/planner.py`, `ai/skills.py`,
`ai/memory.py`, `ai/identity.py`, `ai/recommendation.py`,
`ai/providers/{base,registry,oauth,vault}.py`, `core/`, the queue/user/OAuth
portions of `db.py`, and the delivery + identity test suites.

**Exclude:** `ai/providers/swiggy.py`, `integrations/`, the food-specific system
prompt in `ai/planner.py`, the ordering skills' copy, and every business
document (`PARTNERSHIP.md`, `FEASIBILITY.md`, `LAUNCH.md`, `DEMO.md`).

**Replace before publishing:** the system prompt with a neutral example, the
`Offer` fields with domain-neutral ones (or keep them and document them as an
example schema), and `tests/test_journey.py` with a mock-provider equivalent.

**Check:** run the architecture tests after extraction — they are the fastest
way to confirm nothing product-specific came along.
