# Baileys WhatsApp gateway

The WhatsApp transport for this backend.

```
WhatsApp  ──▶  Baileys  ──▶  POST /webhook/inbound  ──▶  backend
WhatsApp  ◀──  Baileys  ◀──  POST /send             ◀──  backend
```

The backend never imports Baileys and never sees a Baileys object. It posts
`{phone, text}` here; this process turns that into `sock.sendMessage`.
Everything WhatsApp-shaped — JIDs, message keys, `fromMe`, `@lid`, session
credentials — stops at this boundary.

> ⚠️ **Baileys is an unofficial WhatsApp Web protocol client.** It is **not**
> the Meta WhatsApp Business Cloud API, and that is a deliberate choice for this
> prototype. WhatsApp can ban the number. **Use a spare number, never one tied
> to a business account.** A linked device also receives *every* message that
> number gets, so anyone who texts it will be answered.

## Setup

```bash
cd whatsapp-gateway
npm install

cp .env.example .env
node -e "console.log(require('crypto').randomBytes(32).toString('hex'))"
# paste that value into WHATSAPP_GATEWAY_SECRET in BOTH .env files:
#   whatsapp-gateway/.env   and   ../.env

npm start
```

A QR code prints in the terminal. Scan it once:

**WhatsApp → Settings → Linked devices → Link a device**

Then start the backend, from the repo root:

```powershell
venv\Scripts\python.exe -m uvicorn backend.app:app --port 8000
```

Message the linked number. `"I need milk"` should come back as a numbered list.

## QR pairing, and why only once

Credentials land in `auth/` (gitignored) via Baileys' `useMultiFileAuthState`.
They survive restarts, so the QR appears on the **first** run and not again.

To force a fresh pairing, delete the directory:

```bash
rm -rf auth/
```

If the phone unlinks the device, the gateway logs `STOPPED - logged out` and
does **not** reconnect: those credentials are dead and retrying cannot revive
them. Delete `auth/` and restart.

## Routes

| Route | Direction | Auth |
|---|---|---|
| `POST /send` | backend → gateway | `X-Gateway-Secret` |
| `GET /health` | anyone | none — reports connection state only |

```bash
curl -X POST http://localhost:8100/send \
  -H "X-Gateway-Secret: $WHATSAPP_GATEWAY_SECRET" \
  -H "Content-Type: application/json" \
  -d '{"phone": "919876543210", "text": "hello"}'
```

The gateway posts inbound messages to `BACKEND_URL + BACKEND_INBOUND_PATH` with
the same secret:

```json
{
  "message_id": "3EB0C767D82B9F41A1BE",
  "phone": "919876543210",
  "text": "I need milk",
  "timestamp": "1755000000",
  "type": "text"
}
```

`phone` is a bare MSISDN — the one identity format the backend stores. The raw
JID stays in this process so replies go back to the exact chat.

## Configuration

| Variable | Purpose |
|---|---|
| `WHATSAPP_GATEWAY_SECRET` | shared secret, both directions. **Fails closed** — the gateway will not start without it |
| `GATEWAY_PORT` | where `/send` listens (default 8100) |
| `BACKEND_URL` | the backend's base URL (default `http://localhost:8000`) |
| `BACKEND_INBOUND_PATH` | default `/webhook/inbound` |
| `AUTH_DIR` | session credentials (default `auth`) |
| `BACKEND_TIMEOUT_MS` | per-attempt timeout (default 30000) |
| `BACKEND_RETRIES` | attempts after the first (default 4) |
| `DEDUPE_SIZE` | remembered message ids (default 2000) |

## Tests

```bash
npm test
```

51 checks, no framework and no network — `node --test`. The socket is never
opened, because the parts worth testing are pure: normalization, deduplication,
delivery and retry, `/send` auth, and the reconnect decision.

The one that matters most:

```
WhatsApp → gateway → backend → gateway → WhatsApp, without a loop
```

Baileys delivers **our own** replies back through `messages.upsert`. Answering
them puts the assistant in a conversation with itself, forever, spending real
money on every lap. `normalizeMessage` drops anything with `key.fromMe`.

## What is deliberately not here

- **Media sending and downloading.** The backend has no media pipeline — an
  attachment only triggers "I can't open attachments yet" — so the gateway
  *classifies* images, voice notes and documents and passes the type. It does
  not call `downloadMediaMessage`, because nothing consumes the bytes. Building
  that would be writing a producer with no consumer.
- **Delivery and read receipts.** The backend does not ask for them, so
  `messages.update` is not subscribed.
- **Groups, broadcasts and newsletters.** Ignored entirely; this is a
  one-to-one assistant.
