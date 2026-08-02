# AI Food Concierge

A food concierge that lives entirely in WhatsApp. You say what you feel like
eating; it decides *what* and *where*, tells you why, and orders it.

```
You:  I'm hungry.

AI:   Hey — I'm your food concierge 👋 I can find something you'll actually
      want. Connect Swiggy and I'll take it from there:
      https://…

      (after connecting, it picks up where you left off)

AI:   Three good options nearby.

      1. Meghana Foods
      ⭐ 4.6 · ₹340 · 22 min
      You usually go for spicy biryani, so this is right up your street.

      2. Empire Restaurant
      ⭐ 4.3 · ₹280 · 18 min
      Cheaper and quicker, still in budget.

      Which one?

You:  the second one

AI:   Mutton Biryani from Empire, ₹280 — confirm?

You:  yes

AI:   Done. About 18 minutes 🎉 Ask me any time where it's got to.
```

The problem is **decision fatigue**, not ordering mechanics. People bounce
between Swiggy, Zomato, Google and Instagram and still don't know what they
want. Resolving the decision is the hard half; placing the order is the easy one.

**WhatsApp is the entire product.** No dashboard, no admin panel, no web app.
If a feature needs a screen, it's the wrong feature.

---

## Quick start

```bash
git clone <repo> && cd Grovio
python -m venv venv && source venv/Scripts/activate   # Windows: venv\Scripts\activate
pip install -r requirements.txt

cp .env.example .env        # then set GROQ_API_KEY and TOKEN_ENCRYPTION_KEY
uvicorn backend.app:app --reload --port 8000
```

Check it came up correctly:

```bash
curl localhost:8000/health
# {"ready":true,"checks":{"database":"ok","encryption":"ok",...}}
```

`/health` reports what is actually wired up — a green process with no encryption
key is not ready to hold anyone's credentials, and it says so.

> `uvicorn --reload` does **not** watch `.env`. Restart fully after changing it.

### Talking to it locally

The webhook needs a public URL. With [ngrok](https://ngrok.com):

```bash
ngrok http 8000
# set PUBLIC_BASE_URL to the https URL, then point your WhatsApp
# webhook at {PUBLIC_BASE_URL}/webhook and restart uvicorn
```

## Tests

No framework — each suite is a plain script that exits non-zero on failure.

```bash
python tests/test_journey.py      # the whole user journey
python tests/test_identity.py     # identity, OAuth, tokens
python tests/test_concierge.py    # planner, providers, ranking
python tests/test_cloud_api_transport.py
python tests/test_whatsapp_async_delivery.py
```

296 checks, no network, no API keys required. Tests and scripts add the repo
root to `sys.path` themselves, so no `PYTHONPATH` is needed in any shell.

On Windows, if ₹ or emoji raise an encoding error, set
`$env:PYTHONIOENCODING="utf-8"` first — PowerShell has no `VAR=value cmd`
prefix syntax.

Several tests assert **architecture**, not behaviour: they tokenise the upper
layers and fail the build if the planner can so much as name a credential or a
platform. Those are load-bearing — if one fails, a boundary has eroded.

## Architecture

```
WhatsApp  →  webhook            verify, enqueue, return 200 in ms
                 ↓
          whatsapp_worker       durable queue, per-phone ordering, retries
                 ↓
          ai/concierge.py       turn entry point
                 ↓
          ai/planner.py         LLM orchestrates via tools
                 ↓              (knows nothing about OAuth or platforms)
          ai/skills.py          capability + "can this user do it?"
                 ├── ai/memory.py           what we know about them
                 ├── ai/recommendation.py   deterministic scoring + reasons
                 └── ai/providers/registry  routed by CAPABILITY, not by name
                          └── oauth.py + vault.py   linking, refresh, encryption
                                   ↓
                     Swiggy | Zomato | Blinkit | Zepto
```

Three invariants hold the product together:

**1. Never invent.** Restaurant names, ratings, ETAs and prices come from a real
provider response or they don't get said. When no provider can serve a request,
the concierge says so — it does not improvise. There is deliberately no stub
restaurant provider.

**2. The LLM orchestrates; it doesn't decide alone.** Its only levers are the
tools in `planner.TOOLS`. It cannot reach a provider, the database, or a
platform directly. Ranking is deterministic in `recommendation.py` so
explanations can't be rationalised after the fact, and `place_order` takes the
*index* of an option that was actually shown — so it cannot order something it
never offered.

**3. Credentials never leave the provider layer.** `vault.py` is the only module
that decrypts a token. The planner is told a link is *needed*, never what it is.

### Layout

| Path | What it is |
|---|---|
| `backend/routes.py` | The WhatsApp webhook. One capability. |
| `backend/whatsapp_worker.py` | Async delivery: dedup, ordering, retries, restart recovery. The most carefully-built file here — 47 tests. |
| `backend/linking.py` | OAuth callback; resumes the conversation afterwards. |
| `ai/planner.py` | Orchestration and personality. |
| `ai/skills.py` | Capability layer; turns provider reality into plain instructions. |
| `ai/memory.py` | Preferences, history, food memory. |
| `ai/providers/` | Protocol, registry, OAuth, vault, adapters. |
| `core/` | LLM client, crypto, config, logging. |
| `db.py` | SQLite: queue, users, links, orders. |

## Configuration

Every variable is documented in [.env.example](.env.example). The two that
matter most:

| Variable | Why |
|---|---|
| `TOKEN_ENCRYPTION_KEY` | Encrypts provider tokens. **Fails closed** — without it, accounts cannot be linked at all. There is no plaintext fallback. |
| `PUBLIC_BASE_URL` | Where providers send users back after authorising. Must be https in production and whitelisted by the provider. |

## Status

| Phase | Scope | State |
|---|---|---|
| 1–2 | Audit + delete the predecessor ERP | ✅ |
| 3 | Planner, providers, memory, Cloud API transport | ✅ |
| 4 | Identity + provider account linking | ✅ |
| 5 | End-to-end journey: recommend → order → track | ✅ |
| 6 | Production hardening | ✅ |
| — | Restaurant provider | **Blocked on Swiggy Builders Club access** |

The product is architecturally complete and runs end to end on grocery today.
Restaurant search is switched off *by design* until Swiggy's `/food` MCP access
is granted — see [FEASIBILITY.md](FEASIBILITY.md) for why that is an approval
problem rather than an engineering one, and [LAUNCH.md](LAUNCH.md) for exactly
what remains.

## Documentation

**Start here**

| Document | Read it for |
|---|---|
| [ARCHITECTURE.md](ARCHITECTURE.md) | One-page system design |
| [SECURITY.md](SECURITY.md) | OAuth, encryption, isolation, threat model |
| [DEPLOYMENT.md](DEPLOYMENT.md) | Clean machine → running service |
| [CHECKLIST.md](CHECKLIST.md) | Production readiness, with the real blockers marked |

**Product & business**

| Document | Read it for |
|---|---|
| [DEMO.md](DEMO.md) | The end-to-end journey, as a demo script |
| [PARTNERSHIP.md](PARTNERSHIP.md) | Swiggy Builders Club proposal |
| [LAUNCH.md](LAUNCH.md) | What's done, what's blocked, what's left |
| [FEASIBILITY.md](FEASIBILITY.md) | Which providers can legally and technically be integrated |

**Engineering**

| Document | Read it for |
|---|---|
| [TECHDEBT.md](TECHDEBT.md) | Known debt, ranked, with what blocks production |
| [IDENTITY.md](IDENTITY.md) | User, OAuth and token lifecycles |
| [MIGRATION.md](MIGRATION.md) | How this repo became the concierge |
| [README-OSS.md](README-OSS.md) | Extracting the framework as open source |
| [CLAUDE.md](CLAUDE.md) | Conventions and landmines for contributors |
