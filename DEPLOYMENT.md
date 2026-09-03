# Deployment

How to take this from a clean machine to a running service. Written for an
engineer who has never seen the codebase.

**What you are deploying:** one Python process (FastAPI + a background worker)
and one SQLite file. No message broker, no cache, no separate worker service.

---

## 1. Prerequisites

| Requirement | Notes |
|---|---|
| Python 3.11+ | 3.13 tested |
| A public HTTPS URL | Meta and OAuth providers both reject plain http |
| A spare WhatsApp number | For the gateway to link as a device |
| An LLM API key | Groq or any OpenAI-compatible endpoint |
| Swiggy Builders Club access | Only for provider linking — the service runs without it |

---

## 2. Install

```bash
git clone <repo> && cd Grovio
python -m venv venv
source venv/bin/activate          # Windows: venv\Scripts\activate
pip install -r requirements.txt
```

The database directory is created automatically on first run.

## 3. Configure

```bash
cp .env.example .env
```

Generate the encryption key — **do this once, and keep it safe**:

```bash
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

Minimum viable `.env`:

```ini
GROQ_API_KEY=gsk_...
TOKEN_ENCRYPTION_KEY=<the key you just generated>
PUBLIC_BASE_URL=https://your-domain.com

WHATSAPP_TRANSPORT=cloud
WHATSAPP_GATEWAY_SECRET=<the same value as in whatsapp-gateway/.env>
WHATSAPP_GATEWAY_URL=http://localhost:8100
```

> **`TOKEN_ENCRYPTION_KEY` is not recoverable.** Lose it and every linked account
> must be reconnected. Store it in your platform's secret manager, never in the
> repo and never in the database it protects.

Every variable is documented in [.env.example](.env.example).

## 4. Run

```bash
uvicorn backend.app:app --host 0.0.0.0 --port 8000
```

Verify before going further:

```bash
curl https://your-domain.com/health
# {"ready":true,"checks":{"database":"ok","encryption":"ok",...}}
```

`ready:false` returns **HTTP 503** — wire your load balancer to this endpoint so
it never routes traffic to a process that cannot serve. The startup log also
warns explicitly about a missing encryption key or a non-https public URL.

## 5. Connect WhatsApp

The transport is the **Baileys gateway** — a separate Node process in
`whatsapp-gateway/`. There is no platform app to create, no business
verification and no public webhook to expose.

> ⚠️ Baileys is an **unofficial WhatsApp Web protocol client**, not the Meta
> WhatsApp Business Cloud API, and it is used deliberately for this prototype.
> WhatsApp can ban the number. Use a spare number, never one tied to a business
> account. A linked device also receives *every* message that number gets, so
> anyone who texts it will be answered — `AUTHORIZED_PHONES` still gates
> spending.

### 5.1 Generate the shared secret

One secret, in **both** `.env` files. It is what makes the gateway trusted:
there is no signature to fall back on, and it fails closed on both sides.

```bash
python -c "import secrets; print(secrets.token_hex(32))"
```

Put it in `WHATSAPP_GATEWAY_SECRET` in the repo root `.env` **and** in
`whatsapp-gateway/.env`. The two must match exactly.

### 5.2 Start the gateway

```bash
cd whatsapp-gateway
npm install
npm start
```

### 5.3 Scan the QR, once

A QR code prints in the terminal. On the phone:

**WhatsApp → Settings → Linked devices → Link a device**

Credentials are written to `whatsapp-gateway/auth/` (gitignored) and survive
restarts, so this is a one-time step. To force fresh pairing, delete that
directory.

Never commit `auth/`. Anyone holding those files owns the linked device.

### 5.4 Verify

```bash
curl http://localhost:8100/health
# {"status":"connected","stopped":false}
```

Then start the backend and message the linked number. The backend log shows
`[webhook] inbound … from …`, then `[whatsapp] sent text to …`.

If `/health` reports `disconnected`, the QR was not scanned or the session
dropped. If it reports `stopped: true`, the phone unlinked this device — delete
`auth/` and pair again; reconnecting cannot revive dead credentials.

## 6. Connect providers (optional)

Provider linking requires the provider to whitelist your callback URL:

```
https://your-domain.com/link/{provider}/callback
```

For Swiggy this is granted with Builders Club production access — see
[PARTNERSHIP.md](PARTNERSHIP.md). Until then the service runs normally and tells
users it cannot order yet, which is the intended behaviour rather than an error.

---

## 7. Running as a service

### systemd

```ini
# /etc/systemd/system/concierge.service
[Unit]
Description=AI Food Concierge
After=network.target

[Service]
Type=simple
User=concierge
WorkingDirectory=/opt/concierge
EnvironmentFile=/opt/concierge/.env
ExecStart=/opt/concierge/venv/bin/uvicorn backend.app:app --host 0.0.0.0 --port 8000
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl enable --now concierge
sudo journalctl -u concierge -f
```

Restarts are safe: pending work is persisted and re-driven on boot by
`recover_pending()`. In-flight messages are marked failed rather than
reprocessed — deliberately, so a crash cannot double-charge anyone.

### Docker

```dockerfile
FROM python:3.13-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
VOLUME ["/app/database"]
EXPOSE 8000
CMD ["uvicorn", "backend.app:app", "--host", "0.0.0.0", "--port", "8000"]
```

**Mount `/app/database` as a volume.** Without it the SQLite file — every user,
link and order — is lost on redeploy.

### Reverse proxy (nginx)

```nginx
location / {
    proxy_pass http://127.0.0.1:8000;
    proxy_set_header Host $host;
    proxy_set_header X-Forwarded-Proto $scheme;
    proxy_set_header X-Forwarded-Host  $host;
}
```

Both forwarded headers matter: the OAuth callback reconstructs its URL from
them to validate signatures.

---

## 8. Important constraint — run exactly one instance

The delivery worker registry is in-process, and SQLite is a single file. **Two
replicas will process the same message twice.**

Consequences to plan around:

- No horizontal scaling yet
- Deploys are a brief gap, not zero-downtime (queued work resumes on boot)
- Set your platform to *recreate*, never *rolling*, until the datastore moves

This is adequate to roughly 1,000 users. Beyond that, the documented path is
Postgres plus Redis for the worker registry — the queue abstraction already
lives behind `db.py` helpers, so it is a driver change rather than a redesign.
See [TECHDEBT.md](TECHDEBT.md) → Scalability.

---

## 9. Backups

**Nothing backs up the database today.** SQLite with WAL survives a crash, but a
corrupt or deleted file loses every user, credential and order.

Minimum viable, before real users:

```bash
# hourly, atomic, safe while the app is running
sqlite3 database/orders.db ".backup '/backups/orders-$(date +%%Y%%m%%d-%%H).db'"
```

Retain 7 daily + 4 weekly, store off-host, and **test a restore** — an untested
backup is a hypothesis. Note that backups contain encrypted tokens: they are
useless without `TOKEN_ENCRYPTION_KEY`, so back that up separately and in a
different place.

---

## 10. Operations

### Health and logs

```bash
curl -s localhost:8000/health | jq       # readiness
journalctl -u concierge -f                # live logs
```

Logs are currently unstructured text (see [TECHDEBT.md](TECHDEBT.md) H5). To
trace one user's conversation end to end, query the database directly:

```sql
SELECT role, content, created_at FROM conversation_history
 WHERE phone = ? ORDER BY id;

SELECT * FROM whatsapp_outbound WHERE phone = ? ORDER BY id;   -- delivery
SELECT * FROM orders           WHERE phone = ? ORDER BY id;    -- what they bought
```

Conversation replay is genuinely reliable — inbound, outbound, history and
orders together reconstruct any session.

### Common problems

| Symptom | Cause | Fix |
|---|---|---|
| `/health` → 503, `encryption: unconfigured` | No `TOKEN_ENCRYPTION_KEY` | Set it and restart |
| Webhook verification fails | Verify token mismatch, or URL not https | Check both in the Meta dashboard |
| Every inbound message returns 401 | `WHATSAPP_GATEWAY_SECRET` differs between the two `.env` files, or is unset (fails closed) | Make them identical, restart both |
| Users get "I can't search restaurants yet" | Working as designed — no restaurant provider | Awaiting Builders Club access |
| Account linking fails at the provider | Callback URL not whitelisted | Ask the provider to whitelist it |
| Replies stop for one user only | That phone's worker hit an error | Check logs; it self-heals on the next message |
| `database is locked` | Two instances running | Run exactly one (§8) |

### Changing configuration

`uvicorn --reload` does **not** watch `.env`. Always restart fully after editing
it — a stale credential surviving a "reload" has cost real debugging time here.

### Key rotation

Rotating `TOKEN_ENCRYPTION_KEY` invalidates existing links **safely**:
decryption returns nothing, links are marked revoked, and users are offered a
fresh connect. It degrades to re-authorisation, never to an error. Announce it
if you have real users.

---

## 11. Pre-launch

Do not open to the public without working through
[CHECKLIST.md](CHECKLIST.md). Two items there are genuine blockers: **rate
limiting** (unbounded LLM spend per user) and **data deletion** (DPDP/GDPR).
