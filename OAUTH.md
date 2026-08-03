# OAuth discovery — root cause report

**Symptom:** linking failed with `Could not discover OAuth endpoints`, and the
user was told *"Swiggy's connection is temporarily unavailable — try again
shortly."*

Both halves of that message were false. Swiggy was up and serving its metadata
correctly the entire time, and no amount of retrying could ever have fixed it.

---

## 1. Why discovery failed

We asked the wrong URLs. Verified live on 2026-08-03:

| URL | Result |
|---|---|
| `https://mcp.swiggy.com/.well-known/oauth-authorization-server` | **200** — full metadata |
| `https://mcp.swiggy.com/.well-known/oauth-protected-resource` | 404 |
| `https://mcp.swiggy.com/im/.well-known/oauth-authorization-server` | 404 — **what we probed** |

The document lives at the **origin root**. Our discovery only ever appended the
well-known path to the end of the MCP server URL:

```python
server = "https://mcp.swiggy.com/im"
await _fetch_json(client, f"{server}/.well-known/oauth-protected-resource")
#  -> https://mcp.swiggy.com/im/.well-known/oauth-protected-resource   404
```

When that missed, the fallback candidate list was `[server]` — the same
path-suffixed URL again, for a different document. **The origin root was never
tried.** Neither was the form the RFCs actually specify:

> **RFC 8414 §3.1** and **RFC 9728 §3.1**: the well-known segment is inserted
> **between the host and the path** — `https://host/.well-known/doc/im` — not
> appended to the end of it.

So the implementation was wrong on the standard *and* wrong on the common
convention, and Swiggy happens to use the convention we skipped.

The `GET /.well-known/oauth-protected-resource → 200` in the logs is consistent
with this: it is Swiggy's server answering some request 200 while the specific
metadata document we needed was never requested at a URL that serves it.

## 2. Why the user saw "Swiggy is down"

`ai/skills.py`, `_link_prompt`:

```python
except oauth.OAuthError as e:
    return SkillResult(
        SkillStatus.ERROR,
        f"ERROR: the {label} connection is temporarily unavailable. Apologise "
        f"briefly and suggest trying again shortly.",
    )
```

**Every** `OAuthError` produced that one sentence. A missing endpoint, an
expired token, a rate limit and a genuine 500 were indistinguishable by the
time they reached the user.

## 3. Why that message is wrong

Three separate ways:

1. **It is factually false.** Swiggy was serving traffic and serving valid
   metadata. Telling a user a working service is broken is the same class of
   error as inventing a restaurant — it sends them away believing something
   untrue.
2. **The advice cannot work.** "Try again shortly" implies time will fix it. A
   wrong URL is not transient. The user would retry forever.
3. **It hid the bug from us.** A configuration error reported as an outage
   looks like someone else's problem, so nobody investigates.

## 4. How the fix works

### Discovery now probes every legitimate location

```python
def _metadata_urls(url, document):
    #  https://host/.well-known/doc/im   <- RFC 8414 §3.1 / RFC 9728 §3.1
    #  https://host/im/.well-known/doc   <- the appended form (common)
    #  https://host/.well-known/doc      <- the ORIGIN ROOT (what Swiggy uses)
```

Applied to the protected-resource document, every issuer named in it, and the
authorization-server and OIDC documents. Swiggy is now discovered on the third
candidate with no configuration at all.

### Four tiers, most authoritative first

| Tier | Source | When |
|---|---|---|
| 1 | `SWIGGY_OAUTH_AUTHORIZE_URL` / `_TOKEN_URL` | operator pins them; skips the network entirely |
| 2 | discovery | normal path — survives a provider moving its endpoints |
| 3 | endpoints declared on `OAuthConfig` | discovery found nothing |
| 4 | `OAuthNotConfigured` | a **configuration** error, never an outage |

Discovery deliberately outranks the declared defaults: hardcoded endpoints that
silently win would go stale without anyone noticing.

### Swiggy's endpoints are configured from its documentation

Not guessed. Taken from
[mcp.swiggy.com/builders/docs/start/authenticate](https://mcp.swiggy.com/builders/docs/start/authenticate/)
and verified against the live metadata document:

```python
SWIGGY_AUTHORIZE_URL    = "https://mcp.swiggy.com/auth/authorize"
SWIGGY_TOKEN_URL        = "https://mcp.swiggy.com/auth/token"
SWIGGY_REGISTRATION_URL = "https://mcp.swiggy.com/auth/register"
SWIGGY_SCOPES           = ("mcp:tools",)
```

The registration endpoint matters as much as the other two: Swiggy issues
client ids **only** by dynamic registration ("you don't need to apply for or
manage a client identity"). A fallback without it would build an authorization
request with no `client_id` — a fallback that only appears to work.

Both `/im` and `/food` share one authorization server, at the origin.

### Failures are classified

`ai/providers/failures.py` — thirteen categories, each with its own instruction
to the model:

| Category | Cause | What the user is told |
|---|---|---|
| `CONFIGURATION` | discovery failed, 404, tool-surface mismatch | *not ready yet* — never "down", never "try again" |
| `NOT_LINKED` | no account connected | *connect your account* |
| `AUTHENTICATION` | token rejected / expired | *reconnect* |
| `AUTHORIZATION` | linked but not permitted | *no permission for that* |
| `UNAVAILABLE` | 5xx | **the only** "try again shortly" |
| `NETWORK` | connect error | *couldn't get through* |
| `TIMEOUT` | no response in time | *taking unusually long* |
| `RATE_LIMIT` | 429 | *give it a minute* |
| `PARSING` | unreadable response | our bug, said plainly |
| `VALIDATION` | 400/422 | our bug, said plainly |
| `CHECKOUT` | order refused | *nothing was charged* |
| `ITEM_UNAVAILABLE` | sold out / closed | *offer the alternatives* |
| `UNKNOWN` | anything else | no cause guessed, no blame assigned |

"Down" and "misconfigured" are told apart by whether the server **answered at
all**, tracked explicitly on the discovery trail rather than inferred from log
text. Nothing answered → `OAuthUnreachable` (an outage). It answered 404s →
`OAuthNotConfigured` (ours).

Search failures are no longer swallowed either: `registry.search` returns
`(offers, errors)`, so a broken search reports **why** instead of "no results"
— which had been reporting a configuration failure as an empty catalogue.

### Discovery failure is now diagnosable

Every attempt is logged with its outcome, then the fallback decision:

```
[oauth] discovery FAILED for https://mcp.swiggy.com/im. Attempts:
  https://mcp.swiggy.com/.well-known/oauth-protected-resource/im -> HTTP 404
  https://mcp.swiggy.com/im/.well-known/oauth-protected-resource -> HTTP 404
  https://mcp.swiggy.com/.well-known/oauth-protected-resource -> HTTP 404
  401-challenge https://mcp.swiggy.com/im -> HTTP 200, no challenge
  https://mcp.swiggy.com/.well-known/oauth-authorization-server -> HTTP 200 ✓
[oauth] falling back to the endpoints declared by the provider:
  authorize=... token=... — fallback SUCCEEDED, linking can proceed.
```

Which endpoint, why, which fallback, and whether it worked.

## 5. Regression tests

`tests/test_oauth_discovery.py` — **75 checks**:

| § | Pins |
|---|---|
| 1 | the RFC form, the appended form, and **the origin root** are all candidates |
| 2 | a server publishing only at the root is discovered (Swiggy's exact shape) |
| 3 | the four tiers resolve in order; an env pin never touches the network |
| 4 | `CONFIGURATION` never says "unavailable" or "shortly"; only `UNAVAILABLE` does |
| 5 | every category has a distinct instruction; status codes map correctly |
| 6 | what the user is told, per failure class, through the skills layer |
| 7 | a broken search reports why, not "no results" |
| 8 | Swiggy's endpoints match the documentation, on the origin, with DCR |

Plus `test_identity.py` §9: discovery-then-fallback ordering, and a silent
server raising `OAuthUnreachable` rather than `OAuthNotConfigured`.

**614 checks pass across the repo.**

## 6. Still worth knowing

- **No refresh tokens.** Swiggy's docs: *"Refresh-token issuance is not wired in
  v1.0."* Access tokens last 5 days, then the user re-links. `refresh()`
  returning False already routes to a fresh link, so this degrades correctly —
  but expect a re-link prompt every 5 days.
- **This sandbox cannot reach `mcp.swiggy.com`** (ConnectError on every
  attempt), so live discovery could not be exercised from here — the metadata
  above was verified by direct fetch. On a machine with network access,
  discovery should succeed at tier 2 and the fallback never fire. If the log
  shows the fallback being used, linking still works; the trail says why.
- **Production access is still gated.** Redirect URIs are an exact-match
  allowlist, so `PUBLIC_BASE_URL` must be registered with Swiggy. Localhost is
  whitelisted for development.
