# Phase 4 — User Identity & Provider Account Linking

Technical design. Written before implementation, per the phase brief.

**Goal:** let a real stranger start using the product securely. Not
recommendations — the ability to *have* a user at all.

---

## 0. Why this is the phase

`FEASIBILITY.md` established that the blocker is authorisation: a user cannot let
us act on their behalf. That has two halves, and only one is Swiggy's to solve.

| Half | Owner | Status |
|---|---|---|
| Our callback URL whitelisted | Swiggy (Builders Club) | Blocked on their review |
| The linking flow itself | **Us** | **This phase** |

Everything here is buildable and testable now, against a mock provider, with no
approval required. When Swiggy whitelists our callback, the flow is already
built and the provider is a config change.

## 1. Standards, not invention

Nothing below invents a Swiggy API. The MCP SDK already implements **OAuth 2.1
with PKCE (S256)** and exposes a `TokenStorage` protocol. Discovery follows
IETF standards, not vendor-specific paths:

- **RFC 9728** — `/.well-known/oauth-protected-resource` on the MCP server tells
  us which authorization server governs it.
- **RFC 8414** — `/.well-known/oauth-authorization-server` on that server returns
  `authorization_endpoint`, `token_endpoint`, `registration_endpoint`.
- **RFC 7591** — dynamic client registration, when the server offers it.

So the same code links Swiggy today and ONDC later. The provider contributes a
server URL and (optionally) a pre-issued client id; it contributes no protocol.

> **Unverified:** this sandbox cannot reach Swiggy (TLS), so I have not confirmed
> that `mcp.swiggy.com/food` serves these discovery documents. If it does not,
> only `_discover()` changes — the lifecycle, storage and conversation flow are
> unaffected. Verifying is one command against the live server.

## 2. Layering

The brief's requirement, made structural:

```
planner        knows: "the user needs to link something", and a URL to send
   ↓           never sees: tokens, OAuth, provider names
skills         knows: capabilities, and whether the user can use them
   ↓           returns: SkillResult(status, message, link_url)
registry       knows: which providers serve a capability
   ↓
oauth + vault  knows: PKCE, state, refresh, encryption
   ↓
provider       knows: one platform
```

Enforced mechanically — `tests/test_identity.py` fails the build if `oauth`,
`token`, or a platform name appears in `ai/planner.py`.

## 3. Data model

```
users            phone PK, display_name, onboarding_status, created_at, updated_at
user_facts       (exists) long-term preferences — phone+key PK
conversation_history (exists)
food_memory      (exists)

provider_links   phone + provider PK
                 status            LINKED | REVOKED
                 access_token      ENCRYPTED
                 refresh_token     ENCRYPTED
                 expires_at        NULL = never expires
                 scope, client_id, linked_at, updated_at

oauth_states     state PK (256-bit urlsafe)
                 phone, provider, code_verifier (ENCRYPTED)
                 pending_message   the request to resume after linking
                 expires_at        now + 10 min
                 used_at           NULL until consumed — single-use
```

`onboarding_status`: `NEW → LINKED → COMPLETE`.
`NEW` = never linked a provider. `LINKED` = can transact. `COMPLETE` = the few
essential questions have been answered. Everything else is learned, never asked.

**Why tokens live in their own table, not on `users`:** a user may link several
providers, and one provider's revocation must not disturb another's.

## 4. Encryption at rest

`core/crypto.py`, Fernet (AES-128-CBC + HMAC) from `cryptography` — already a
dependency. We do not roll our own.

- Key from `TOKEN_ENCRYPTION_KEY` (Fernet key, 32 url-safe base64 bytes).
- **Fails closed.** No key ⇒ `encrypt()` raises. There is no plaintext fallback,
  because a fallback is how plaintext tokens reach production.
- Encrypted: access tokens, refresh tokens, and PKCE code verifiers. A stolen DB
  file yields nothing usable without the key, which lives outside the DB.

## 5. Lifecycles

### 5.1 User

```
inbound message
   └─ identity.get_or_create(phone)      idempotent, first message creates
        └─ every turn is tied to this user; the phone number is the identity
```
No signup, no password, no profile screen. WhatsApp already authenticated them
by controlling the number.

### 5.2 OAuth (the core flow)

```
1  skill needs a capability
2  vault says: not linked (or refresh failed)
3  oauth.begin(phone, provider, pending_message)
       generate code_verifier + S256 challenge
       generate state (secrets.token_urlsafe(32))
       persist oauth_states row, expires in 10 min
       return authorize_url
4  planner sends the URL in a natural sentence
5  user taps → provider consent → GET /link/{provider}/callback?code&state
6  oauth.complete(state, code)
       claim state ATOMICALLY (UPDATE ... WHERE used_at IS NULL) ← replay guard
       validate: exists, unexpired, provider matches
       exchange code + verifier at token_endpoint
       encrypt + upsert provider_links, status=LINKED
       onboarding_status: NEW → LINKED
7  resume: re-enqueue pending_message as a normal inbound
8  the worker answers the ORIGINAL request; user never repeats themselves
```

Step 6's atomic claim is the same optimistic-claim pattern the delivery worker
uses. Two concurrent callbacks with one state: exactly one wins.

### 5.3 Token refresh

Lazily, on use — never on a timer:

```
access_token(phone, provider)
  ├ no row / REVOKED        → NeedsLink
  ├ expires_at in > 60s     → return it
  ├ refresh_token present   → refresh, re-encrypt, store, return
  └ refresh fails/absent    → mark REVOKED → NeedsLink
```
A 60-second skew guard means a token cannot expire mid-request.

### 5.4 Conversation

```
message → identity → planner → skill
                                 ├ OK          → normal reply
                                 └ NEEDS_LINK  → "connect your account: <url>"
                                                 (original request stored)
```

The planner receives `NEEDS_LINK` as *information*, not a token. It phrases the
sentence; it cannot see or leak credentials.

### 5.5 Failure recovery

| Failure | Behaviour |
|---|---|
| Expired access token | Refreshed silently on next use |
| Refresh rejected (user revoked at provider) | Marked REVOKED, reconnect link offered |
| Invalid/unknown state | 400, nothing mutated |
| Replayed state | 400 — single-use claim already consumed |
| Expired state (>10 min) | 400 with "link expired, ask me again" |
| Provider down mid-link | State expires harmlessly; user retries |
| Restart mid-link | State is in the DB — the link still completes |
| Duplicate linking | Upsert on (phone, provider); one row, latest tokens win |

Nothing here holds an in-memory future or an open socket waiting for a human.
The flow is durable across restarts because the state lives in SQLite, which is
the same reason the delivery queue survives restarts.

## 6. Onboarding

First contact, before any linking:

> Hi 👋 I'm your food concierge. I can find food you'll actually want — and once
> you connect your Swiggy account I can order it too. <link>

After linking, ask **only** what cannot be inferred, one at a time, in
conversation — never as a form:

- where they usually order to (home)
- budget for a typical meal
- allergies or dietary rules  ← the only safety-critical one
- anything they never want to see

Office location, cuisines, gym days, weekday-vs-weekend habits: **learned**, not
asked. The brief is explicit and it is also correct — every question is friction,
and `ai/memory.py` already records preferences the moment they surface in
conversation ("I don't like mushrooms" → stored immediately, correctable later).

## 7. Security checklist

| Control | Implementation |
|---|---|
| PKCE S256 | `oauth.begin` — protects the code even if the redirect leaks |
| State validation | Random 256-bit, bound to phone + provider |
| Replay prevention | Single-use atomic claim on `used_at` |
| Short-lived states | 10-minute expiry |
| Encryption at rest | Fernet; fails closed with no key |
| No token exposure | Vault is the only reader; planner/skills never see tokens |
| Logout / unlink | Deletes tokens, sets REVOKED |
| Callback hygiene | Provider mismatch, unknown state and expiry all 400 |
| No secrets in logs | Tokens/verifiers never logged; states logged truncated |
| HTTPS redirect URI | `PUBLIC_BASE_URL` must be https in production |

## 8. What this phase does NOT do

- No restaurant recommendations (explicitly out of scope).
- No invented provider endpoints — discovery is standards-based and the Swiggy
  provider stays a thin declaration.
- No real Swiggy linking until Builders Club whitelists our callback. Until
  then the flow is exercised end-to-end against a mock provider in tests, which
  is exactly how it will behave with a real one.

## 9. Test plan

| Case | Assertion |
|---|---|
| New user | Created on first message, status NEW |
| Existing user | Reused, not duplicated; updated_at moves |
| Not linked | Skill returns NEEDS_LINK with a URL, no crash |
| Successful link | Tokens encrypted, status LINKED, ciphertext ≠ plaintext |
| Conversation continuation | Original request re-queued; user doesn't repeat |
| Expired token | Refreshed transparently |
| Revoked token | Refresh failure → REVOKED → reconnect offered |
| Invalid callback | Unknown state → 400, nothing mutated |
| Replayed callback | Second use → 400 |
| Expired state | → 400 |
| Duplicate linking | One row, latest tokens |
| Multiple providers | Independent; revoking one leaves the other LINKED |
| Unlink | Tokens gone, status REVOKED |
| Layering | `ai/planner.py` contains no oauth/token/platform references |
