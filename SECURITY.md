# Security Architecture

How this system handles a user's delivery account, and why that handling is
safe. Written for a security reviewer.

Every mechanism below is implemented and covered by tests. Gaps are stated in
[§10](#10-known-gaps) rather than omitted.

---

## 1. Threat model

We hold OAuth tokens that can **spend a user's money**. That is the asset worth
protecting. Everything else follows from it.

| Adversary | Concern |
|---|---|
| Attacker with the database file | Reading or replaying stored credentials |
| Attacker on the network | Intercepting an authorisation code or webhook |
| Malicious user | Reaching another user's data, or making the agent misbehave |
| A compromised or confused LLM | Being steered into unauthorised spending |
| Us, accidentally | Logging a token, leaking one upward, or storing plaintext |

The last two are the ones most systems in this category get wrong, so they are
addressed structurally rather than by convention.

---

## 2. OAuth flow

**OAuth 2.1, authorization-code grant with PKCE (S256).** No password, OTP or
card detail ever reaches this system — and the assistant is explicitly
instructed never to ask for one.

```
1  User asks for something requiring their account
2  System generates:
       code_verifier   64 bytes, secrets.token_urlsafe
       code_challenge  SHA-256(verifier), base64url
       state           32 bytes, secrets.token_urlsafe
   and persists them, bound to (phone, provider), TTL 10 minutes
3  User receives an authorisation URL in WhatsApp
4  User authorises on the provider's own domain
5  Provider redirects to  {PUBLIC_BASE_URL}/link/{provider}/callback?code&state
6  System atomically CLAIMS the state (single use), validates it,
   exchanges code + verifier for tokens, encrypts, stores
7  The user's original request is answered automatically
```

**Endpoint discovery is standards-based, never hardcoded:**

- **RFC 9728** — `/.well-known/oauth-protected-resource` names the authorization server
- **RFC 8414** — `/.well-known/oauth-authorization-server` returns the endpoints

We invent no provider API. A provider that publishes no metadata declares its
endpoints explicitly instead; the flow is identical either way.

**Why PKCE matters here.** The redirect passes through a mobile browser opened
from a chat message. If that URL leaks — shoulder-surfed, logged by an
intermediary, in browser history — the authorization code alone is useless: the
token exchange also requires the verifier, which never leaves our server.

---

## 3. Replay protection

The state token is **single-use, enforced atomically in the database**:

```sql
UPDATE oauth_states SET used_at = CURRENT_TIMESTAMP
 WHERE state = ? AND used_at IS NULL
```

The claim succeeds only if `rowcount == 1`. Two concurrent callbacks carrying
the same state — a replay, a double-tap, a retrying proxy — produce exactly one
winner; the loser receives a generic 400 and nothing mutates.

Additional constraints on every state:

- 256 bits of `secrets` entropy
- Bound to a specific phone **and** provider; a state minted for one cannot link another
- 10-minute TTL, checked after the claim
- Expired and unknown states are indistinguishable in the response

*Tested: replayed callback, expired state, unknown state, provider mismatch,
missing code — each rejected with nothing mutated.*

---

## 4. Encryption at rest

**Fernet** (AES-128-CBC + HMAC-SHA256) from `cryptography`. We do not roll our
own primitives.

Encrypted: access tokens, refresh tokens, and PKCE code verifiers.

**The system fails closed.** With no `TOKEN_ENCRYPTION_KEY`, encryption raises
and accounts simply cannot be linked. There is deliberately **no plaintext
fallback** — a fallback is precisely how unencrypted tokens reach production.
`/health` returns 503 while encryption is unconfigured, so a load balancer will
not route traffic to a process that cannot protect credentials.

The key lives in the environment, never in the database it protects. A stolen
database file yields no usable credential. Rotating the key invalidates existing
links safely: decryption returns `None`, the link is marked revoked, and the
user is offered a fresh connect — it degrades to re-authorisation, never to an
error or a plaintext read.

---

## 5. Token storage and lifecycle

One module — the **vault** — decrypts tokens. Nothing else in the codebase does.

```
access_token(phone, provider)
  ├ no link / revoked          → NeedsLink        (carries no credential)
  ├ expires in > 60s           → return
  ├ refresh token present      → refresh, re-encrypt, store, return
  └ refresh rejected           → mark REVOKED → NeedsLink
```

- **Lazy refresh, never on a timer.** Tokens are refreshed only when used, which
  keeps the window of live credentials as small as possible.
- **60-second skew guard** so a token cannot expire mid-request.
- **Refresh rejection is treated as revocation** — the user revoked us at the
  provider, and only fresh consent restores access.
- **Unlink deletes credentials outright**, not just a status flag.

---

## 6. User isolation

Identity is the WhatsApp phone number, authenticated by the platform. There are
no passwords, sessions or cookies to steal.

Every table is keyed by phone: `users`, `user_facts`, `conversation_history`,
`food_memory`, `provider_links`, `oauth_states`, `offer_sessions`, `orders`.
There is no shared mutable per-user state anywhere in the process. Reviewed
explicitly for cross-user leakage; the only global caches hold public provider
metadata.

Two users cannot see each other's history, preferences, orders, or offers — and
one user's provider outage or revoked token has no effect on another's session.

---

## 7. Constraining the LLM

The most important control: **the model's only levers are a fixed tool set.** It
cannot reach a provider, the database, the filesystem or a credential directly.

| Control | Effect |
|---|---|
| Fixed tool schema | No arbitrary code, queries or HTTP |
| Ordering by **index** | Can only order an option a provider actually returned |
| Deterministic ranking | Cannot rationalise a recommendation after the fact |
| Capability gating | An unavailable capability returns an honest refusal, not a guess |
| Credentials invisible | Told a link is *needed*, never what it is |
| Bounded tool loop | A confused model cannot spin indefinitely |

**Prompt injection.** Stored preferences enter the system prompt, so a user can
in principle write instructions into their own profile. The blast radius is
bounded to that user — facts are keyed by phone and isolation holds — and the
money path is protected independently: index-based ordering means a poisoned
prompt still cannot conjure an order for something no provider returned. Values
are length-capped to remove the bulk-instruction variant. Full fencing of stored
facts is tracked in [TECHDEBT.md](TECHDEBT.md) H4.

---

## 8. Webhook verification

| Transport | Verification |
|---|---|
| WhatsApp Cloud API | `X-Hub-Signature-256` HMAC-SHA256 over the raw body, `hmac.compare_digest` |

Both **fail closed**: a missing app secret rejects every request rather than
silently accepting forged webhooks. The subscription handshake echoes
`hub.challenge` only on an exact verify-token match.

Signature comparison is constant-time. Failures log the reconstructed URL — the
genuinely useful diagnostic — and never the signature, secret or body.

---

## 9. Secrets and data handling

- `.env` is gitignored; no secret has ever been committed. `.env.example`
  documents every variable with no values.
- Message content is logged **only** when `DEBUG` is explicitly enabled. Tokens,
  refresh tokens and PKCE verifiers are never logged under any setting.
- Provider error bodies never reach the user, and never reach the model. The
  user sees a plain apology; details go to server logs.
- The OAuth callback page echoes no token, no state and no internal detail.
- Personal data held: phone number, conversation history, food preferences, and
  a coarse home area if volunteered. No payment instrument, no address book, no
  location tracking.

---

## 10. Known gaps

Stated plainly; both are tracked with fixes scoped in [TECHDEBT.md](TECHDEBT.md).

| Gap | Status |
|---|---|
| **No rate limiting** (H1) | A single number can drive unbounded LLM spend. Blocks public launch. |
| **No user data deletion** (H2) | DPDP/GDPR erasure has no code path. Blocks public launch. |
| Unbounded history retention (H3) | Pairs with the above. |
| Prompt-injection fencing (H4) | Partially mitigated; bounded blast radius. |
| Structured logging / audit trail (H5) | Orders and links are timestamped and reconstructable, but there is no dedicated security-event log. |
| Database backups | SQLite + WAL survives a crash; a corrupt file currently loses everything. |

None require architectural change.

---

## 11. Why this architecture is safe

1. **The dangerous thing is isolated.** One module touches credentials, and a
   test fails the build if that changes.
2. **Every control fails closed.** Missing encryption key, missing webhook
   secret, missing provider — each denies rather than degrades.
3. **The LLM is contained by construction, not by prompt.** Index-based ordering
   means the worst-case output of a compromised prompt is a wrong *choice among
   real options*, never invented spending.
4. **Nothing is invented.** No fabricated venue, price or status can reach a
   user, because the capability layer refuses rather than improvises.
5. **The invariants are executable.** Layering, isolation and honesty are
   asserted by tests, so they cannot quietly erode.
