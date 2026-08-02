# Swiggy Builders Club — Partnership Proposal

**What we are asking for:** production access to the Swiggy Food MCP server, and
whitelisting of one OAuth redirect URI.

**What we have already built:** a complete, tested AI food concierge that runs
end to end on the Instamart MCP today. The restaurant path is implemented up to
the provider boundary and is switched off until access exists.

---

## 1. Why this product exists

People do not struggle to *order* food. They struggle to *decide*.

The pattern is familiar: open Swiggy, scroll, get overwhelmed, check Zomato,
search Google, look at Instagram, return to Swiggy twenty minutes later and
order the same thing as last week. The friction is not the checkout — Swiggy
solved checkout years ago. The friction is the decision.

We built the layer that resolves the decision, in the place people already are:
WhatsApp. A user types "I'm hungry" and gets a specific, reasoned recommendation
built from real Swiggy catalogue data — then confirms, and the order is placed
through Swiggy's own MCP interface.

We are not building a marketplace. We have no supply, no delivery fleet, no
restaurant relationships, and no ambition to acquire any. We are a decision
layer that ends in a Swiggy order.

---

## 2. How this benefits Swiggy

**Incremental orders, not cannibalised ones.** Our value shows up precisely when
a user would otherwise have browsed and abandoned. Decision fatigue is a real
source of session drop-off; converting those sessions is upside.

**A conversational surface Swiggy does not have to build.** Users increasingly
expect to accomplish things by asking. Swiggy shipping MCP servers signals this
is understood. We are the kind of client that investment is meant to attract —
one that drives real transactions rather than demos.

**Recommendations that always terminate in a Swiggy order.** There is no
comparison-shopping step and no competing checkout. The concierge does not
present alternatives from other platforms alongside Swiggy's.

**Higher-intent, better-informed baskets.** Because we know a user's budget,
dietary constraints and history, the options we surface are ones they are likely
to accept. Fewer abandoned carts, fewer allergy-driven cancellations.

**A demonstrable reference implementation.** If Swiggy wants to point at
somebody using the MCP stack correctly and safely, this is a codebase built to
be inspected — architecture, security and threat model all documented, with the
guarantees enforced by tests rather than asserted in prose.

---

## 3. Why we are using MCP correctly

We use the MCP servers exactly as designed: as an authenticated, user-consented
interface, through the official client libraries.

| Practice | What we do |
|---|---|
| Authentication | OAuth 2.1 + PKCE, per user, via Swiggy's own consent screen |
| Discovery | RFC 8414/9728 metadata — no endpoint is hardcoded or guessed |
| Tool usage | Only documented tools, called through the official MCP client |
| Session hygiene | One session per user, reused; dropped and reconnected cleanly on failure |
| Rate discipline | One search per user request; results cached for the conversation, not warehoused |
| Data retention | We store what a user's own history requires, never a copy of Swiggy's catalogue |
| Failure handling | Errors are logged server-side and never shown to users; no retry storms |

**We have written no adapter against unverified tools.** We know the Food server
covers restaurant search, menus, ordering and tracking, but we do not know its
exact tool names — so we have not guessed them. Inventing a provider API is
against this project's own rules, and the restaurant capability reports itself
unavailable rather than fabricating results.

---

## 4. Why this is not scraping

This distinction matters to us, and we have made architectural choices to
guarantee it rather than merely claim it.

| | Scraping | This system |
|---|---|---|
| Access path | HTML/undocumented endpoints | Official MCP server |
| Authentication | Evaded or impersonated | User's own OAuth consent |
| Identity | Disguised, rotated proxies | Identified client, one session per user |
| Rate behaviour | Aggressive, bot-like | One call per user request |
| Data handling | Bulk extraction, warehoused | Per-request, never mirrored |
| Terms | Violated | Followed |
| Whose account | Nobody's, or a fake one | The individual user's own |

**We hold no scraped Swiggy data and no bulk catalogue copy.** Every order is
placed on a user's own account, with their own explicit OAuth consent, which
they can revoke at any time from their side — and which we honour immediately by
marking the link revoked and asking them to reconnect.

There is also a commercial argument we take seriously: scraping would forfeit
exactly the partnership we are asking for. We would rather be slow and
sanctioned than fast and unwelcome.

---

## 5. Expected usage

Honest projections. We would rather revise these upward later than overstate now.

| Stage | Users | Orders/day | MCP calls/day (est.) |
|---|---|---|---|
| Private pilot | 20–50 | 10–30 | ~200 |
| Beta | 500 | 150–300 | ~2,500 |
| Year 1 target | 5,000 | 1,500–3,000 | ~25,000 |

Roughly 3–6 MCP calls per completed order: search, then cart and checkout, plus
occasional status. We do not poll, we do not pre-fetch, and we do not maintain a
background sync. Calls happen only when a human is actively in a conversation.

We will honour any rate limit you set and will implement per-user throttling on
our side before opening beta.

---

## 6. Security guarantees

Full detail in [SECURITY.md](SECURITY.md). The commitments that matter to you:

1. **We never see a Swiggy password, OTP or payment instrument.** Authentication
   happens entirely on Swiggy's domain. The assistant is explicitly instructed
   never to ask for credentials.
2. **Tokens are encrypted at rest** (Fernet, AES-128-CBC + HMAC), with the key
   held outside the database. The system fails closed: with no key configured,
   accounts cannot be linked at all — there is no plaintext fallback.
3. **One module can decrypt a token**, and a test fails the build if any other
   layer can even name one.
4. **Authorisation state is single-use and replay-proof**, enforced by an atomic
   database claim, bound to one phone and one provider, expiring in 10 minutes.
5. **The AI cannot invent an order.** Ordering takes the index of an option a
   provider actually returned — not a free-text name — so no prompt can produce
   spending on an item that does not exist in your catalogue response.
6. **Revocation is honoured immediately.** A rejected refresh is treated as
   revocation; we stop using the grant and ask the user to reconnect.
7. **We surface no Swiggy error text to users**, and never expose internal
   detail, report ids or provider messages.

---

## 7. What we are asking for

| # | Ask | Why |
|---|---|---|
| 1 | Production access to the Food MCP server | The restaurant journey is built and waiting |
| 2 | Whitelist one redirect URI: `{PUBLIC_BASE_URL}/link/swiggy/callback` | Without it no user can complete OAuth against our service |
| 3 | Confirmation of the Food server's tool surface | So we implement against real tools rather than guesses |
| 4 | Guidance on rate limits and payment methods | For capacity planning and checkout UX |

We are happy to walk any Swiggy engineer through the codebase, the threat model,
or a live demo. The repository is written to be read.

**Contact:** builders@swiggy.in application accompanying this document.

---

## Appendix — supporting documents

| Document | Contents |
|---|---|
| [ARCHITECTURE.md](ARCHITECTURE.md) | One-page system design |
| [SECURITY.md](SECURITY.md) | OAuth, encryption, isolation, threat model |
| [DEMO.md](DEMO.md) | The end-to-end user journey |
| [FEASIBILITY.md](FEASIBILITY.md) | Our provider analysis, including why we chose MCP over every alternative |
| [TECHDEBT.md](TECHDEBT.md) | Known gaps, ranked and unhidden |
