# Migration report: Twilio → WhatsApp Business Cloud API

> **HISTORICAL RECORD — SUPERSEDED.** This documents the Twilio → WhatsApp
> Business Cloud API migration. The Cloud API transport has since been replaced
> by the Baileys WhatsApp gateway (`whatsapp-gateway/`, `whatsapp/gateway.py`).
> Kept because it records why the phone-key format and the fail-closed posture
> are what they are; nothing in it describes the current transport.

Complete replacement. Twilio is gone — not deprecated, not behind a flag, not
kept as a fallback. There is one messaging provider.

## Files removed

| File | Why |
|---|---|
| `whatsapp/twilio.py` | the Twilio transport (29 references) |
| `whatsapp/transport.py` | the seam that chose between two providers — with one provider it was indirection for its own sake |
| `tests/test_cloud_api_transport.py` | superseded by `tests/test_cloud_api.py` |

## Files added

| File | Purpose |
|---|---|
| `whatsapp/__init__.py` | the seam. `from whatsapp import send_text` — nothing above imports `cloud_api` directly |
| `tests/test_cloud_api.py` | 130 checks across the whole messaging layer |
| `MIGRATION_CLOUD_API.md` | this report |

## Files changed

| File | Change |
|---|---|
| `whatsapp/cloud_api.py` | rewritten: send_text/image/document/template, mark_read, status + receipt parsing, canonical phone, hardened parsing |
| `backend/routes.py` | the Twilio webhook branch deleted; statuses, receipts and account errors handled |
| `backend/whatsapp_worker.py` | imports `whatsapp`, not `whatsapp.transport` |
| `backend/app.py` | startup warning for missing Cloud API config; health reports the messaging provider |
| `db.py` | `record_delivery_status`, `_migrate_phone_keys`; Twilio comments corrected |
| `core/authz.py` | allowlist compares digits, so `+91 97…` and `919 7…` match |
| `requirements.txt` | `twilio==9.10.9` removed |

## Dependencies

| | |
|---|---|
| **Removed** | `twilio==9.10.9` |
| **Added** | none — the Cloud API is plain HTTP over the `httpx` already present |

## Environment variables

| Variable | Change |
|---|---|
| `TWILIO_ACCOUNT_SID` | **removed** |
| `TWILIO_AUTH_TOKEN` | **removed** |
| `TWILIO_WHATSAPP_FROM` | **removed** |
| `WHATSAPP_TRANSPORT` | **removed** — there is nothing to choose between |
| `WHATSAPP_API_VERSION` | **renamed** to `META_API_VERSION` |
| `META_API_VERSION` | new name, default `v23.0` (was `v21.0`) |
| `WHATSAPP_APP_ID` | new, optional — reference only |
| `WHATSAPP_ACCESS_TOKEN` | unchanged, now required |
| `WHATSAPP_PHONE_NUMBER_ID` | unchanged, now required |
| `WHATSAPP_APP_SECRET` | unchanged, **fails closed** |
| `WHATSAPP_VERIFY_TOKEN` | unchanged, **fails closed** |

## Breaking changes

### 1. Phone numbers change format — and this one bites

**Not in the original scope, and the most dangerous part of the migration.**

Twilio delivered `whatsapp:+917795871481`. The Cloud API delivers
`917795871481`. Every table is keyed by phone, so at cutover the same human
becomes a **different user**: no memory, no order history, no linked Swiggy
account, no onboarding state.

Your live database had exactly this — every row under `whatsapp:+917795871481`.

Fixed at both ends:

- `whatsapp.canonical_phone()` normalises everything at ingress. One human, one
  key, whatever wrote the row.
- `db._migrate_phone_keys()` rewrites existing rows across all ten phone-keyed
  tables on startup. Idempotent, and it refuses to merge when both formats
  exist — silently combining two people's histories is worse than a stale row.

### 2. `WHATSAPP_TRANSPORT` is ignored

Remove it from `.env`. It selects nothing.

### 3. The webhook speaks JSON, not form-encoded

`POST /webhook` now requires `X-Hub-Signature-256` and a JSON body. The Twilio
form path and its TwiML response are gone. **Re-point the webhook in the Meta
App Dashboard** — a Twilio console URL will no longer be called.

### 4. Error codes in `whatsapp_outbound.error_code` change meaning

Twilio's `63038`/`21211` become Meta's `131047`/`131026`. Old rows keep their
old numbers; nothing reads them retroactively.

## What did NOT change

Untouched, as required: planner, skills, memory, conversation engine, provider
registry, OAuth, restaurant ordering, Instamart ordering. The delivery worker's
guarantees — per-phone ordering, dedup, retry classification, restart recovery —
are unchanged; only the function it calls to send is different.

## New capability

- **Delivery and read receipts** — `whatsapp_outbound` advances
  SENT → DELIVERED → READ, forward-only, so a late `delivered` cannot un-read a
  message.
- **Interactive replies** — button and list taps arrive as the user's text, so
  "1" from a button is the same as "1" typed.
- **Media and templates** — `send_image`, `send_document`, `send_template` exist
  and are tested. Nothing calls them yet; templates are the only way to message
  outside the 24-hour window (error 131047).
- **`mark_read`** — never raises; a failed receipt must not affect a reply. A
  typing indicator is one field on this same call.

## Error handling

| Condition | Classification | Behaviour |
|---|---|---|
| 401 / code 190 | expired token | **permanent** — no retry can mint a token |
| 403 / code 200 | permission | **permanent** — configuration |
| 429 | rate limit | **retry** with backoff |
| 5xx | Meta outage | **retry** |
| 131047 | outside 24h window | **permanent** — needs a template |
| 131026 | not on WhatsApp | **permanent** |
| 400 / 4xx | our malformed request | **permanent** |
| timeout / connect error | network | **retry** |
| missing credentials | `NotConfigured` | **permanent** |

## Security

- `X-Hub-Signature-256` verified with `hmac.compare_digest` — constant time, so
  a fast reject can't leak the correct prefix byte by byte.
- Both the app secret and the verify token **fail closed**. Unset means every
  webhook is rejected, not accepted.
- **Replay:** `whatsapp_inbound.message_sid` is UNIQUE. Meta redelivers on any
  non-200, so the same message id arriving twice is normal — it is enqueued
  once. Verified by test.
- Payloads are treated as hostile: every level is type-checked, never indexed.
  `{"messages": 7}` yields nothing instead of raising inside the handler.
- Access tokens are never logged. Meta's error bodies are, because a failure
  you can't read is a failure you can't fix — the token is only ever in a
  request header.

## Verification

```powershell
venv\Scripts\python.exe tests\test_cloud_api.py               # 130 checks
venv\Scripts\python.exe tests\test_whatsapp_async_delivery.py #  50 checks
```

Full repo: **633 checks, all green.**

Coverage per the migration brief: webhook verification, incoming text,
outgoing text, delivery receipt, read receipt, status update, invalid
signature, expired token, rate limit, retry, unknown events — plus interactive
replies, the phone-key migration, replay, and a build-failing check that no
Twilio identifier survives anywhere in application code.

### Going live

1. Remove `WHATSAPP_TRANSPORT` and the three `TWILIO_*` variables from `.env`.
2. Set `WHATSAPP_ACCESS_TOKEN`, `WHATSAPP_PHONE_NUMBER_ID`,
   `WHATSAPP_APP_SECRET`, `WHATSAPP_VERIFY_TOKEN`. Startup warns for any that
   are missing; the last two fail closed, so the product is silent without them.
3. Meta App Dashboard → WhatsApp → Configuration → Webhook:
   `https://<your-host>/webhook`, same verify token, subscribe to **messages**.
4. Start the app and check the log for the phone-key migration line if you are
   carrying a Twilio-era database.
5. Message the number. `GET /health` should show
   `"messaging": "whatsapp_cloud_api"` and `"messaging_configured": true`.

### One caveat

The 24-hour customer service window is a real behaviour change. Twilio's
sandbox let you reply whenever; the Cloud API returns **131047** if you reply
more than 24 hours after the user's last message. That is classified as
permanent and will not be retried. Sending outside the window needs an approved
template — `send_template` is ready, but no template is registered yet.
