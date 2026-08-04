# Swiggy redirect-URI whitelisting

Not a code task — a human one. Two things to do, in this order.

## 1. Get a stable domain first

Swiggy's allowlist is **exact-match, no wildcards**. A free ngrok host changes
on every restart, so anything whitelisted today stops working tomorrow. Do not
submit a `*.ngrok-free.app` URL.

Options, cheapest first:

| Option | Cost | Notes |
|---|---|---|
| ngrok reserved domain | free tier includes 1 static domain | `ngrok http 8000 --domain=yourname.ngrok.app` |
| Cloudflare Tunnel + your own domain | free if you own a domain | stable, no session limits |
| Deploy (Railway / Fly / Render) | ~free tier | what you need for real users anyway |

Then set it and restart:

```
PUBLIC_BASE_URL=https://your-stable-domain
```

The exact callback URLs are printed at startup — copy them from there rather
than typing them:

```
[providers] swiggy_instamart redirect URI: https://.../link/swiggy_instamart/callback
[providers] swiggy_food      redirect URI: https://.../link/swiggy_food/callback
```

## 2. File the request

Open an issue at **https://github.com/Swiggy/swiggy-mcp-server-manifest/issues**
(this is the documented route — issues #23, #28 and #46 are the same request
from other builders). `builders@swiggy.in` is the email alternative.

Template — replace the two URLs with what startup printed:

> **Title:** Feature request: whitelist `<your-domain>` as an OAuth redirect URI
>
> **What we're building**
> A conversational food concierge that runs entirely inside WhatsApp. Users say
> what they feel like eating; it decides what and where, explains why, and
> places the order on their own linked Swiggy account. There is no dashboard —
> WhatsApp is the whole product.
>
> **Redirect URIs to whitelist**
> ```
> https://<your-domain>/link/swiggy_instamart/callback
> https://<your-domain>/link/swiggy_food/callback
> ```
>
> **Servers used:** `/im` (Instamart) and `/food`
> **Scopes:** `mcp:tools`
> **Client registration:** dynamic (RFC 7591) via `/auth/register`
> **PKCE:** S256
>
> Every user links their own Swiggy account; we store only their encrypted
> OAuth tokens and never see credentials. Orders are placed by the user's own
> explicit confirmation in chat, cash on delivery.

## Meanwhile: test on the laptop, keep the tunnel

These are two different URLs, and only one of them is `PUBLIC_BASE_URL`:

| What | Where it's configured | Needs a public URL? |
|---|---|---|
| WhatsApp webhook (inbound) | Twilio / Meta console | **Yes** — they must reach you |
| OAuth callback | `PUBLIC_BASE_URL` | No, if you finish the link on this machine |

So keep the tunnel pointed at the webhook and set:

```
PUBLIC_BASE_URL=http://localhost:8000
```

`http://localhost` is whitelisted for development, so linking works today with
no whitelisting request at all. Restart, and open the link the concierge sends
**in a browser on the machine running the server** — WhatsApp Desktop or Web
makes that one click. It cannot work from your phone: `localhost` there is the
phone.

This unblocks you, not your users. Real users need steps 1 and 2.
