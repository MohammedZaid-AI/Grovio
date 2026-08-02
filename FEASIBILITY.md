# Product Feasibility Audit

**Question:** what actually blocks a real customer from using this product?
**Answer:** not architecture, and not AI. It is **authorisation** — we have no
legal way for a stranger to let us act on their behalf.

Everything else in this document is downstream of that one sentence.

---

## 0. Correction to the Phase 1 audit

`MIGRATION.md` §0 states that Swiggy publishes no restaurant-ordering API. **That
is now wrong**, and the decision you made off the back of it (deep-link handoff)
should be revisited.

On **27 January 2026** Swiggy shipped official MCP servers across three verticals:

| Endpoint | Vertical |
|---|---|
| `https://mcp.swiggy.com/food` | Food delivery |
| `https://mcp.swiggy.com/im` | Instamart (what we already use) |
| `https://mcp.swiggy.com/dineout` | Dineout / table booking |

Per Swiggy's own manifest, the Food server covers **restaurant search, menu
browsing, cart management, ordering, and order tracking (COD only)** — the entire
capability set this product needs, from the platform that actually has the
supply.

I found only the Instamart endpoint in Phase 1 because that is the only one the
repo referenced. I did not check whether siblings existed. That was my error, and
it is the difference between "this product is blocked on a data source" and "this
product is blocked on a form."

---

## 1. What data do we need for real recommendations?

The promise — *"Truffles, because you order grilled chicken on weekdays, it fits
your ₹350 budget, is rated 4.7, and delivers in 18 minutes"* — decomposes into
seven distinct feeds:

| Need | Used for |
|---|---|
| Venue identity + location | "which restaurant", distance |
| Menu + item pricing | "which dish", budget fit |
| Ratings / reviews | quality signal |
| Delivery ETA | "18 minutes" |
| Live availability | not recommending a closed kitchen |
| Order placement | the actual product |
| Order tracking | post-purchase |

Ratings and ETA come from **different sources** at most providers. Any
architecture that gets six of seven still cannot complete a sentence like the one
above without either dropping a claim or inventing it.

---

## 2–7. Provider capability matrix

Verified against primary sources (vendor docs and policies), not blog posts.

| Provider | Search | Menus | Ratings | Reviews | ETA | Pricing | Avail. | Order | Track | Official API | Scraping needed | Automation stance |
|---|:--:|:--:|:--:|:--:|:--:|:--:|:--:|:--:|:--:|---|---|---|
| **Swiggy Food MCP** | ✅ | ✅ | ✅ | ⚠️ | ✅ | ✅ | ✅ | ✅ | ✅ | **Yes** (MCP) | No | Sanctioned, approval-gated |
| **Swiggy Instamart MCP** | ✅ | n/a | ❌ | ❌ | ⚠️ | ✅ | ✅ | ✅ | ✅ | **Yes** (MCP) | No | Same |
| **Swiggy Dineout MCP** | ✅ | ⚠️ | ✅ | ⚠️ | n/a | ⚠️ | ✅ | ✅ (booking) | ⚠️ | **Yes** (MCP) | No | Same |
| **ONDC** | ✅ | ✅ | ⚠️ | ❌ | ✅ | ✅ | ✅ | ✅ | ✅ | **Yes** (Beckn) | No | Sanctioned, open network |
| **Zomato** | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ | **No** (public API discontinued) | Yes | Bot-protected |
| **Magicpin** | ✅¹ | ✅¹ | ⚠️ | ❌ | ✅¹ | ✅¹ | ✅¹ | ✅¹ | ✅¹ | No public developer API | — | Reachable *via ONDC* |
| **Google Places** | ✅ | ❌ | ✅ | ✅ | ❌ | ⚠️² | ✅³ | ❌ | ❌ | **Yes** (REST) | No | Sanctioned, heavily conditioned |
| **OpenStreetMap** | ✅ | ❌ | ❌ | ❌ | ❌ | ❌ | ⚠️⁴ | ❌ | ❌ | **Yes** (Overpass) | No | Open (ODbL) |

¹ Magicpin is a **seller** app (BPP) on ONDC, not a buyer-side API vendor. Its
~150,000 daily orders are reachable *only* by becoming an ONDC buyer app.
² `price_level` is a 0–4 bucket, not item pricing.
³ `opening_hours` / `business_status` only — not live kitchen availability.
⁴ `opening_hours` tag where contributors have filled it in; coverage is uneven.

### 5. Which have official APIs
Swiggy (all three MCP servers), ONDC (Beckn protocol), Google Places,
OpenStreetMap/Overpass. **Zomato does not** — the public v2.1 developer API is
discontinued and self-registration is closed; what remains is a merchant-side
POS/partner programme, which is the wrong side of the marketplace for us.

### 6. Which require scraping
Zomato and Magicpin, for consumer-side data. Both would be unauthorised.

### 7. Which prohibit automation
- **Zomato / Swiggy web properties:** both run commercial bot protection and
  actively fingerprint scrapers. I was unable to retrieve Zomato's ToS text
  directly (the request failed), so I am **not** quoting a clause — but the
  presence of active countermeasures is itself the operative signal, and
  scraping is inconsistent with an approval-based programme we intend to join.
- **Google Places:** automation is fine; **storage is not**. Ratings, reviews and
  names must be fetched live and displayed with "Google Maps" attribution. Only
  `place_id` may be stored indefinitely; lat/lng for ≤30 days. This is the single
  most under-appreciated constraint in this document — see §9.

### 8. Suitable for production
**Swiggy MCP** (once approved) and **ONDC** (once onboarded). Google Places is
production-suitable for *discovery only*, under its caching rules. Zomato and
Magicpin are not viable as direct integrations.

---

## The actual blocker, stated precisely

Swiggy's manifest whitelists these OAuth redirect URIs and no others:

```
claude://claude.ai/settings/connectors
https://chatgpt.com/connector_platform_oauth_redirect
https://claude.ai/api/mcp/auth_callback
https://insiders.vscode.dev/redirect
https://oauth.pstmn.io/v1/callback
https://vscode.dev/redirect
http://localhost  ·  http://localhost/callback
http://127.0.0.1  ·  http://127.0.0.1/callback
```

and states:

> "Third-party app development is not permitted at this time due to ongoing
> security reviews and compliance requirements."
> "Contact us if you need additional URIs whitelisted."

Three consequences fall straight out of this:

1. **The product works today — for exactly one person.** `http://localhost` is
   whitelisted, which is why real Instamart orders already go through from this
   machine. That is the sanctioned prototype path.
2. **It cannot serve a second user.** A stranger cannot complete OAuth against
   our WhatsApp service, because our callback URL is not whitelisted. No amount
   of engineering changes this.
3. **The fix is an application, not a sprint.** Swiggy's Builders Club:
   prototype locally free → apply for production with a demo → security review →
   production credentials. Contact: `builders@swiggy.in`.

**We are one email and one security review away from a legitimate product, and
zero lines of code away from it.** Writing more features before sending that
email is optimising the wrong constraint.

### Second-order consequence: account linking is a product surface

Even after approval, every user must OAuth-link their own Swiggy account. That
is a real flow we have not designed: WhatsApp → "tap to connect your Swiggy
account" → OAuth → callback → bind token to phone number → refresh/expiry
handling → revocation. It also implies **per-user credential storage**, which
raises the security bar on the whole system considerably.

There is also an operational warning worth designing around: Swiggy states that
using the app while an MCP session is active "may cause session conflicts or
order processing issues."

---

## 9–12. Architecture comparison

### Option A — Swiggy MCP (Food + Instamart + Dineout)

| | |
|---|---|
| **Advantages** | Only option delivering all seven feeds through one integration. Ordering *and* tracking included. We already run a working MCP client, so this is a new adapter, not new infrastructure. Real supply — the restaurants people actually want. |
| **Disadvantages** | Single-vendor dependency. Approval-gated and revocable. COD-only ordering today. Per-user OAuth linking required. Session conflicts with the Swiggy app. |
| **Legal risk** | **Low once approved** — explicitly sanctioned. **High if we ship to third parties before approval**, which the manifest forbids in plain language. |
| **Engineering effort** | **Low.** ~1 week: new `FoodProvider` adapter, plus the account-linking flow (the larger half). |
| **Scalability** | Swiggy's, with rate limits "expandable on request". |
| **Maintenance** | Low — MCP is a versioned contract, not a scraped DOM. |
| **Long-term viability** | **Medium.** Excellent while the relationship holds; existential if it doesn't. |

### Option B — ONDC Buyer App (BAP)

| | |
|---|---|
| **Advantages** | Vendor-neutral by construction — one integration reaches every seller app on the network, including Magicpin (~150k orders/day). Government-backed open protocol; nobody can unilaterally cut us off. 600+ cities. Strategically the only option where our access is not somebody's business decision. |
| **Disadvantages** | Materially harder: Ed25519 signing keys, X25519 encryption keys, `ondc-site-verification.html` domain proof, an `/on_subscribe` decrypt-and-echo callback, registry whitelisting across staging → pre-prod → prod, and the async Beckn transaction flow (search/select/init/confirm/status/track) which is callback-based, not request/response. Requires a **Network Participant Agreement** — i.e. a legal entity. Thinner per-restaurant volume: 5–15 orders/day vs 40–50 on Swiggy. Ratings are inconsistent across sellers. |
| **Legal risk** | **Lowest of all options** — participation is the intended use. |
| **Engineering effort** | **High.** Realistically 4–8 weeks, most of it protocol plumbing and onboarding, before a single recommendation ships. |
| **Scalability** | Network-wide; grows as ONDC grows (15–20% MoM). |
| **Maintenance** | Medium — protocol versioning, multiple seller-app quirks. |
| **Long-term viability** | **Highest.** |

### Option C — Google Places discovery + deep-link handoff (the Phase 1 decision)

| | |
|---|---|
| **Advantages** | Ships fast, clean legally, no approval gate, works for any user immediately. Genuine ratings and reviews. |
| **Disadvantages** | **No menus, no item pricing, no delivery ETA, no ordering, no tracking.** The concierge cannot say "18 minutes" or "₹350" — the two things that make a recommendation actionable. Ratings/reviews **cannot be cached**, so every recommendation costs a live billed API call, and attribution must appear in every message. Hands the user off at the moment of value capture. |
| **Legal risk** | **Low, but conditional** — the caching prohibition is easy to violate accidentally by "optimising" with a cache. |
| **Engineering effort** | **Low.** ~3–4 days. |
| **Scalability** | Cost scales linearly with conversations, since caching is disallowed. |
| **Maintenance** | Low. |
| **Long-term viability** | **Low as an end state** — it is a demo of the decision layer, not a business. Good as a *fallback*. |

### Option D — Scraping Swiggy / Zomato

| | |
|---|---|
| **Advantages** | Full data, no approval, works immediately. |
| **Disadvantages** | Breaks without warning; needs residential proxies and fingerprint evasion to stay up. |
| **Legal risk** | **High.** Unauthorised access to a platform we are simultaneously asking for partnership with. |
| **Engineering effort** | Deceptively low to start, unbounded to maintain. |
| **Scalability / maintenance** | Poor / continuous. |
| **Long-term viability** | **None.** It also **actively destroys Option A** — being caught scraping is disqualifying in a security review. |

**Rejected.** Not on squeamishness: it forecloses the option we actually want.

### Option E — Hybrid: Google Places (discovery) + Swiggy MCP (execution)

| | |
|---|---|
| **Advantages** | Richest reviews/ratings layered on real ordering. Discovery works even for venues outside Swiggy. |
| **Disadvantages** | Two integrations, two failure modes, and a **hard entity-resolution problem**: matching a Google place to a Swiggy restaurant by name and location is fuzzy, and a wrong match means ordering from the wrong restaurant. Places ratings can't be cached, so the cost compounds. |
| **Legal risk** | Low (both sanctioned). |
| **Engineering effort** | Medium-high — the matching layer is the expensive part, not the APIs. |
| **Long-term viability** | Medium. Justified only once Swiggy's own ratings prove insufficient. |

### Option F — Zomato partner / POS programme
**Not applicable.** It is a merchant-side integration for restaurants operating
*on* Zomato. We are on the consumer side. Wrong end of the marketplace.

### Option G — OpenStreetMap
**Supplementary only.** Free and open (ODbL, attribution + share-alike), useful
for geocoding and neighbourhood context. No ratings, no menus, no ordering.
Cannot carry the product.

---

## Answers to the direct questions

**9. What is the best architecture if we cannot directly order food?**
Option C — real discovery data plus a deep link, with the honest framing "here's
what to get and where; tap to confirm." But this is now the *contingency*, not
the plan, because we probably **can** order directly.

**10. Should the AI deep-link instead of placing orders?**
**No — not as the target architecture.** That was the right call in Phase 1 given
what I then believed. With `mcp.swiggy.com/food` confirmed to support ordering
and tracking, deep-linking would mean deliberately abandoning the product's
second half. Keep it as the fallback for pre-approval and for providers that
never support ordering.

**11. Can ONDC replace Swiggy long-term?**
**Yes — and it is the only option that makes us structurally independent.** But
not first. It is 4–8 weeks of protocol work plus a legal entity, to reach a
network with materially thinner per-restaurant volume. Building it *before*
validating that anyone wants the concierge would be optimising for an
independence problem we have not yet earned.

**12. Compared above.**

---

## Recommendation

> **Swiggy MCP (Food + Instamart) as the execution provider, obtained through
> Builders Club production access, behind the provider abstraction we already
> built — with ONDC as the planned second provider, not a replacement.**

Why this one:

1. **It is the only option that delivers a complete product in one integration.**
   Everything else drops at least one of ETA, pricing, menus, or ordering.
2. **The blocker is administrative, not technical.** We have a working MCP
   client; `/food` is a sibling endpoint. The expensive part is account linking,
   not integration.
3. **The provider layer already anticipated this.** `ai/providers/` routes by
   capability, `registry.supports()` gates honestly, and `test_concierge.py` §10
   fails the build if a platform name leaks upward. Adding ONDC later is
   additive — a second `Provider` implementation, nothing above it changes. That
   is the design paying for itself.
4. **ONDC is the hedge, sequenced second.** Single-vendor risk is real, and the
   answer is a second provider once we have users — not a 4–8 week protocol
   project before we have one.

### Sequence

| Step | Action | Blocks on |
|---|---|---|
| **0** | Email `builders@swiggy.in`: describe the concierge, request production access **and whitelisting for our WhatsApp OAuth callback URL**. | Nothing. Do this first. |
| **1** | Verify the `/food` tool surface: point `inspect_tools.py` at `https://mcp.swiggy.com/food` and capture the real tool names. | Nothing — sanctioned local prototyping. |
| **2** | Build the `FoodProvider` adapter against `/food`, tested via localhost OAuth (single user, permitted). | Step 1 |
| **3** | Design + build OAuth account linking: WhatsApp → link → token bound to phone → refresh, expiry, revocation. Encrypted at rest. | Swiggy approval for a real callback URL |
| **4** | Ship to real users. | Steps 0 + 3 |
| **5** | ONDC BAP as the second provider. | Traction, and a legal entity |

**Do not, while waiting:** register a stub restaurant provider that returns
invented venues, or scrape. The first breaks the product's integrity rule; the
second forfeits step 0.

---

## What I could not verify

Stated plainly, so nothing here is mistaken for confirmed:

- **Exact `/food` tool names and signatures.** I have Swiggy's capability
  description, not a verbatim tool list — the manifest doesn't enumerate them and
  this sandbox cannot reach Swiggy (TLS). **Step 1 resolves this in ten minutes
  on your machine.**
- **Zomato's ToS clause text.** The policy page failed to load; I did not quote
  it and did not infer it.
- **Whether an individual can sign the ONDC Network Participant Agreement**, or
  whether an incorporated entity is mandatory. Strongly implied, not confirmed.
- **Current Google Places pricing** for the relevant SKUs.
- **Swiggy MCP rate limits.** Documented only as "generous, expandable on
  request" — no numbers published.

---

## Sources

- [Swiggy MCP server manifest (official)](https://github.com/Swiggy/swiggy-mcp-server-manifest)
- [Swiggy Builders Club](https://mcp.swiggy.com/builders/)
- [Swiggy press release — Builders Club](https://www.swiggy.com/corporate/press-release/swiggy-to-launch-builders-club-giving-developers-and-enterprises-access-to-its-ai-commerce-stack/)
- [ONDC participant onboarding (official developer docs)](https://github.com/ONDC-Official/developer-docs/blob/main/registry/Onboarding%20of%20Participants.md)
- [ONDC technical resources](https://resources.ondc.org/tech-resources)
- [ONDC network policy](https://resources.ondc.org/ondc-network-policy)
- [Google Places API policies (caching + attribution)](https://developers.google.com/maps/documentation/places/web-service/policies)
- [Google Maps Platform service terms](https://cloud.google.com/maps-platform/terms/maps-service-terms)
- [Zomato API policy](https://www.zomato.com/policies/api-policy/)
- [Magicpin — largest ONDC food app, 150k orders/day](https://www.business-standard.com/companies/start-ups/magicpin-largest-food-delivery-app-on-ondc-logs-150-000-orders-daily-124100800892_1.html)
- [Swiggy opens chatbot food ordering (MCP launch coverage)](https://www.aicerts.ai/news/consumer-ordering-automation-swiggy-opens-chatbot-food-ordering/)
