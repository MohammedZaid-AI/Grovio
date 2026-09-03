"""
OAuth discovery, endpoint fallback, and failure classification.

The bug these pin: linking failed with "Could not discover OAuth endpoints",
and the user was told **"Swiggy's connection is temporarily unavailable, try
again shortly"**. Both halves were wrong. Swiggy was up and serving metadata
the whole time — we were asking the wrong URL — and no amount of retrying was
ever going to fix a URL.

Two root causes, pinned separately:

  1. RFC 8414 §3.1 / RFC 9728 §3.1 put the well-known segment BETWEEN the host
     and the path. We appended it to the end and never tried the origin root,
     which is where Swiggy actually publishes.
  2. Every OAuthError became one sentence: "temporarily unavailable". A
     configuration mistake of ours was reported as the provider being down.

No network. Every fetch is faked.
"""
import base64
import hashlib
import asyncio
import os
import sys
import tempfile
from unittest.mock import AsyncMock, patch
from urllib.parse import parse_qs, urlparse

sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import httpx
from cryptography.fernet import Fernet

os.environ["TOKEN_ENCRYPTION_KEY"] = Fernet.generate_key().decode()
# Importing the provider modules pulls in mcp_use, which otherwise tries to
# post telemetry and stalls the suite on SSL retries.
os.environ["MCP_USE_ANONYMIZED_TELEMETRY"] = "false"
os.environ.setdefault("WHATSAPP_ACCESS_TOKEN", "test-token")
os.environ.setdefault("WHATSAPP_PHONE_NUMBER_ID", "555000111")

import db

_tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
_tmp.close()
db.DB_PATH = _tmp.name
db.init_db()

from ai import identity, skills
from ai.providers import ProviderKind, failures, oauth, registry, swiggy, swiggy_food
from ai.providers.failures import Failure

_passed = _failed = 0


def check(name, condition):
    global _passed, _failed
    if condition:
        _passed += 1
        print(f"  ✅ {name}")
    else:
        _failed += 1
        print(f"  ❌ {name}")


def run(coro):
    return asyncio.run(coro)


METADATA = {
    "issuer": "https://mcp.example/auth",
    "authorization_endpoint": "https://mcp.example/auth/authorize",
    "token_endpoint": "https://mcp.example/auth/token",
    "registration_endpoint": "https://mcp.example/auth/register",
}


def serving(*urls):
    """A _fetch_json that answers ONLY at the given URLs, recording every try.

    Sets `trail.responded`, because the server IS up — it just 404s everywhere
    but the URLs listed. That flag is what separates "misconfigured" from
    "down", so a fake that ignored it would test the wrong branch.
    """
    tried = []

    async def fetch(client, url, trail):
        tried.append(url)
        trail.append(url)
        trail.responded = True
        return METADATA if url in urls else None

    fetch.tried = tried
    return fetch


# ======================================================================
print("\n[1] Metadata URLs follow the RFCs, and include the origin root")
urls = oauth._metadata_urls("https://mcp.swiggy.com/im", "oauth-authorization-server")

check("RFC 8414 form: well-known BETWEEN host and path",
      "https://mcp.swiggy.com/.well-known/oauth-authorization-server/im" in urls)
check("the appended form is still tried (many servers use it)",
      "https://mcp.swiggy.com/im/.well-known/oauth-authorization-server" in urls)
check("ROOT CAUSE: the origin root is tried — where Swiggy actually publishes",
      "https://mcp.swiggy.com/.well-known/oauth-authorization-server" in urls)
check("no duplicates", len(urls) == len(set(urls)))

rootless = oauth._metadata_urls("https://auth.example", "oauth-authorization-server")
check("a pathless server yields just the one URL",
      rootless == ["https://auth.example/.well-known/oauth-authorization-server"])

# ======================================================================
print("\n[2] REGRESSION: a server publishing only at the root is discovered")
# Exactly Swiggy's shape: /im 404s, the root serves the document, and there is
# no protected-resource document at all.
oauth.clear_discovery_cache()
fetch = serving("https://mcp.example/.well-known/oauth-authorization-server")
with patch.object(oauth, "_fetch_json", new=fetch), \
     patch.object(oauth, "_challenge_resource_metadata", new=AsyncMock(return_value=None)):
    found = run(oauth.discover(oauth.OAuthConfig(server_url="https://mcp.example/im")))

check("endpoints were discovered", found["token_endpoint"] == METADATA["token_endpoint"])
check("the path-suffixed URL was tried first",
      fetch.tried[0].startswith("https://mcp.example/.well-known/oauth-protected-resource"))
check("and the root was reached",
      "https://mcp.example/.well-known/oauth-authorization-server" in fetch.tried)

oauth.clear_discovery_cache()
fetch = serving("https://mcp.example/.well-known/oauth-authorization-server/im")
with patch.object(oauth, "_fetch_json", new=fetch), \
     patch.object(oauth, "_challenge_resource_metadata", new=AsyncMock(return_value=None)):
    found = run(oauth.discover(oauth.OAuthConfig(server_url="https://mcp.example/im")))
check("the strict RFC form is discovered too",
      found["token_endpoint"] == METADATA["token_endpoint"])

# ======================================================================
print("\n[3] The four resolution tiers, in order")
oauth.clear_discovery_cache()
os.environ["T_AUTH"] = "https://pinned.example/a"
os.environ["T_TOKEN"] = "https://pinned.example/t"
config = oauth.OAuthConfig(
    server_url="https://mcp.example/im",
    authorize_url_env="T_AUTH", token_url_env="T_TOKEN",
    authorize_url="https://declared.example/a", token_url="https://declared.example/t",
)
never = serving()   # discovery would find nothing
with patch.object(oauth, "_fetch_json", new=never):
    pinned = run(oauth.discover(config))
check("1. an operator env pin wins outright",
      pinned["authorization_endpoint"] == "https://pinned.example/a")
check("   and does not touch the network at all", never.tried == [])

os.environ.pop("T_AUTH"); os.environ.pop("T_TOKEN")
oauth.clear_discovery_cache()
fetch = serving("https://mcp.example/.well-known/oauth-authorization-server")
with patch.object(oauth, "_fetch_json", new=fetch), \
     patch.object(oauth, "_challenge_resource_metadata", new=AsyncMock(return_value=None)):
    discovered = run(oauth.discover(config))
check("2. discovery beats the declared defaults (so a moved endpoint still works)",
      discovered["authorization_endpoint"] == METADATA["authorization_endpoint"])

oauth.clear_discovery_cache()
with patch.object(oauth, "_fetch_json", new=serving()), \
     patch.object(oauth, "_challenge_resource_metadata", new=AsyncMock(return_value=None)):
    declared = run(oauth.discover(config))
check("3. declared defaults take over when discovery finds nothing",
      declared["authorization_endpoint"] == "https://declared.example/a")

oauth.clear_discovery_cache()
bare = oauth.OAuthConfig(server_url="https://mcp.example/im")
try:
    with patch.object(oauth, "_fetch_json", new=serving()), \
         patch.object(oauth, "_challenge_resource_metadata", new=AsyncMock(return_value=None)):
        run(oauth.discover(bare))
    check("4. with no fallback at all it raises OAuthNotConfigured", False)
except oauth.OAuthNotConfigured as e:
    check("4. with no fallback at all it raises OAuthNotConfigured", True)
    check("   and the error names every URL that was tried", "well-known" in str(e))

# ======================================================================
print("\n[4] A configuration failure is NEVER an outage")
check("OAuthNotConfigured classifies as CONFIGURATION",
      failures.classify(oauth.OAuthNotConfigured("x")) is Failure.CONFIGURATION)
check("OAuthUnreachable classifies as UNAVAILABLE",
      failures.classify(oauth.OAuthUnreachable("x")) is Failure.UNAVAILABLE)

instruction = failures.INSTRUCTION[Failure.CONFIGURATION]
check("the CONFIGURATION instruction forbids blaming the provider",
      "NOT down" in instruction)
check("it forbids telling the user to try again",
      "retrying will NOT help" in instruction.lower() or "not help" in instruction.lower())
check("only the UNAVAILABLE instruction says to try again shortly",
      "shortly" in failures.INSTRUCTION[Failure.UNAVAILABLE]
      and "shortly" not in instruction)

# A server that answers nothing at all IS down, and should say so.
oauth.clear_discovery_cache()


async def dead(client, url, trail):
    trail.append(f"{url} -> ConnectError")
    return None


try:
    with patch.object(oauth, "_fetch_json", new=dead), \
         patch.object(oauth, "_challenge_resource_metadata", new=AsyncMock(return_value=None)):
        run(oauth.discover(oauth.OAuthConfig(server_url="https://dead.example/im")))
    check("a totally unreachable server raises UNREACHABLE, not misconfigured", False)
except oauth.OAuthUnreachable:
    check("a totally unreachable server raises UNREACHABLE, not misconfigured", True)
except oauth.OAuthNotConfigured:
    check("a totally unreachable server raises UNREACHABLE, not misconfigured", False)

# ======================================================================
print("\n[5] Every category maps to a distinct, correct instruction")
for failure in Failure:
    check(f"{failure.value} has an instruction", bool(failures.INSTRUCTION.get(failure)))
check("no two categories share wording",
      len({i[:60] for i in failures.INSTRUCTION.values()}) == len(Failure))

check("HTTP 401 -> authentication", failures.from_status(401) is Failure.AUTHENTICATION)
check("HTTP 403 -> authorization", failures.from_status(403) is Failure.AUTHORIZATION)
check("HTTP 404 -> configuration (an endpoint we were told exists, doesn't)",
      failures.from_status(404) is Failure.CONFIGURATION)
check("HTTP 429 -> rate limit", failures.from_status(429) is Failure.RATE_LIMIT)
check("HTTP 500 -> unavailable", failures.from_status(500) is Failure.UNAVAILABLE)
check("HTTP 503 -> unavailable", failures.from_status(503) is Failure.UNAVAILABLE)
check("HTTP 400 -> validation, not an outage", failures.from_status(400) is Failure.VALIDATION)

check("a timeout is a timeout",
      failures.classify(httpx.ReadTimeout("slow")) is Failure.TIMEOUT)
check("a connect error is a network failure",
      failures.classify(httpx.ConnectError("refused")) is Failure.NETWORK)
check("an unreadable payload is a parsing failure",
      failures.classify(ValueError("bad json")) is Failure.PARSING)

from ai.providers.base import ItemUnavailable, ProviderError
check("a sold-out item is its own category",
      failures.classify(ItemUnavailable("gone")) is Failure.ITEM_UNAVAILABLE)
check("a checkout refusal is its own category",
      failures.classify(ProviderError("checkout failed for x")) is Failure.CHECKOUT)

from integrations.swiggy.swiggy_food_mcp import ToolSurfaceMismatch
check("a tool-surface mismatch is configuration, not an outage",
      failures.classify(ToolSurfaceMismatch(["a"], ["b"])) is Failure.CONFIGURATION)

# ======================================================================
print("\n[6] What the user is actually told")


class Linkable:
    name = "test_provider"
    display_name = "TestCo"
    kind = ProviderKind.GROCERY
    oauth = oauth.OAuthConfig(server_url="https://mcp.example/im")

    async def search(self, query, ctx):
        return []


registry.clear()
registry.register(Linkable())
PHONE = "919900011122"
user = identity.load(PHONE)

for error, label, forbidden in (
    (oauth.OAuthNotConfigured("no endpoints"), "configuration", ("temporarily unavailable", "shortly")),
    (oauth.OAuthUnreachable("dead"), "outage", ()),
):
    with patch.object(oauth, "begin", new=AsyncMock(side_effect=error)):
        result = run(skills.connect_provider(user, "test_provider"))
    for phrase in forbidden:
        check(f"{label}: never says {phrase!r}", phrase not in result.message.lower())
    check(f"{label}: the provider is named for context", "TestCo" in result.message)

with patch.object(oauth, "begin", new=AsyncMock(side_effect=oauth.OAuthNotConfigured("x"))):
    result = run(skills.connect_provider(user, "test_provider"))
check("configuration failures get their own status",
      result.status == skills.SkillStatus.CONFIGURATION)
check("the model is told this is OUR problem", "OUR side" in result.message)

with patch.object(oauth, "begin", new=AsyncMock(side_effect=oauth.OAuthUnreachable("x"))):
    result = run(skills.connect_provider(user, "test_provider"))
check("a genuine outage is still reported as one",
      "GENUINELY DOWN" in result.message)
check("and is NOT flagged as a configuration problem",
      result.status != skills.SkillStatus.CONFIGURATION)

# ======================================================================
print("\n[7] A broken search reports WHY, not 'no results'")


class Broken:
    name = "broken_provider"
    display_name = "BrokenCo"
    kind = ProviderKind.GROCERY

    def __init__(self, error):
        self.error = error

    async def search(self, query, ctx):
        raise self.error


registry.clear()
registry.register(Broken(ToolSurfaceMismatch(["update_cart"], ["other"])))
result = run(skills.find_food(user, "milk", "grocery"))
check("a configuration failure is not reported as an empty catalogue",
      result.status == skills.SkillStatus.CONFIGURATION)
check("and never claims the provider is down",
      "temporarily unavailable" not in result.message.lower())

registry.clear()
registry.register(Broken(httpx.ConnectError("refused")))
result = run(skills.find_food(user, "milk", "grocery"))
check("a network failure IS reported as one", "COULD NOT REACH" in result.message)

registry.clear()


class Empty:
    name = "empty_provider"
    display_name = "EmptyCo"
    kind = ProviderKind.GROCERY

    async def search(self, query, ctx):
        return []


registry.register(Empty())
result = run(skills.find_food(user, "unobtainium", "grocery"))
check("a genuinely empty result is still EMPTY, not an error",
      result.status == skills.SkillStatus.EMPTY)

# ======================================================================
print("\n[8] Swiggy is configured from its documentation, not a guess")
for provider, label in ((swiggy.SwiggyInstamartProvider, "instamart"),
                        (swiggy_food.SwiggyFoodProvider, "food")):
    config = provider.oauth
    check(f"{label}: declares a fallback authorize endpoint",
          config.authorize_url == "https://mcp.swiggy.com/auth/authorize")
    check(f"{label}: declares a fallback token endpoint",
          config.token_url == "https://mcp.swiggy.com/auth/token")
    check(f"{label}: requests the documented tool scope",
          config.scopes == ("mcp:tools",))
    check(f"{label}: endpoints are on the ORIGIN, not under the server path",
          "/im/" not in config.authorize_url and "/food/" not in config.authorize_url)
    check(f"{label}: can be pinned by an operator without a code change",
          config.authorize_url_env and config.token_url_env)
    # Swiggy issues client ids by DCR only. A fallback without the registration
    # endpoint builds an authorize URL with no client_id — it would fail anyway,
    # just later and with a different error.
    check(f"{label}: the fallback carries the registration endpoint",
          config.declared.get("registration_endpoint")
          == "https://mcp.swiggy.com/auth/register")

check("both Swiggy servers share one authorization server",
      swiggy.SwiggyInstamartProvider.oauth.token_url
      == swiggy_food.SwiggyFoodProvider.oauth.token_url)

# Swiggy publishes at the root, so its own config must reach the root.
check("discovery for /im would probe the Swiggy root",
      "https://mcp.swiggy.com/.well-known/oauth-authorization-server"
      in oauth._metadata_urls("https://mcp.swiggy.com/im", "oauth-authorization-server"))
check("discovery for /food would probe the Swiggy root",
      "https://mcp.swiggy.com/.well-known/oauth-authorization-server"
      in oauth._metadata_urls("https://mcp.swiggy.com/food", "oauth-authorization-server"))

print("\n" + "=" * 70)
# ======================================================================
print("\n[9] REGRESSION: one registration server, two providers")
# Swiggy runs ONE registration endpoint for both its MCP servers. Caching the
# issued client id by endpoint ALONE handed whichever provider registered first
# its client_id to the other — whose redirect_uri that client was never
# registered against. The server answers exactly that with:
#   {"error":"invalid_request",
#    "error_description":"client_id and redirect_uri are required"}
# Reproduced against the live server 2026-09-03.
SHARED = {
    "authorization_endpoint": "https://mcp.swiggy.com/auth/authorize",
    "token_endpoint": "https://mcp.swiggy.com/auth/token",
    "registration_endpoint": "https://mcp.swiggy.com/auth/register",
}

registered = []


class Registration:
    """A server that issues a client id BOUND to the redirect_uri it was sent."""

    status_code = 201

    def json(self):
        return {"client_id": f"cid::{registered[-1]['redirect_uris'][0]}"}


async def _register(url, json=None, **kw):
    registered.append(json)
    return Registration()


def _registering_client():
    client = AsyncMock()
    client.__aenter__.return_value = client
    client.__aexit__.return_value = False
    client.post = _register
    return client


registered.clear()
oauth._client_cache.clear()
with patch.object(oauth, "discover", AsyncMock(return_value=SHARED)), \
     patch.object(httpx, "AsyncClient", return_value=_registering_client()):
    grocery_url = run(oauth.begin("919000000009", "swiggy_instamart",
                                  swiggy.SwiggyInstamartProvider().oauth))
    food_url = run(oauth.begin("919000000009", "swiggy_food",
                               swiggy_food.SwiggyFoodProvider().oauth))

grocery_params = parse_qs(urlparse(grocery_url).query)
food_params = parse_qs(urlparse(food_url).query)

check("each provider registers for itself, not once for both",
      len(registered) == 2)
check("every authorization URL carries a client_id",
      "client_id" in grocery_params and "client_id" in food_params)
check("every authorization URL carries a redirect_uri",
      "redirect_uri" in grocery_params and "redirect_uri" in food_params)
check("the two providers get DIFFERENT client ids",
      grocery_params["client_id"][0] != food_params["client_id"][0])
check("the grocery client is bound to the grocery callback",
      grocery_params["client_id"][0].endswith(grocery_params["redirect_uri"][0]))
check("the food client is bound to the FOOD callback — this was the bug",
      food_params["client_id"][0].endswith(food_params["redirect_uri"][0]))
check("the redirect_uri is the one the flow needs, unchanged",
      food_params["redirect_uri"][0]
      == "http://localhost:8000/link/swiggy_food/callback")
check("PKCE still travels with it",
      food_params["code_challenge_method"] == ["S256"]
      and bool(food_params["code_challenge"][0]))
check("and each link gets its own state",
      grocery_params["state"][0] != food_params["state"][0])

# The cache must still WORK: the same provider twice must not re-register.
_before = len(registered)
with patch.object(oauth, "discover", AsyncMock(return_value=SHARED)), \
     patch.object(httpx, "AsyncClient", return_value=_registering_client()):
    run(oauth.begin("919000000009", "swiggy_food",
                    swiggy_food.SwiggyFoodProvider().oauth))
check("asking twice for the SAME provider reuses its registration",
      len(registered) == _before)

# A configured client id must still beat registration entirely.
os.environ["SWIGGY_OAUTH_CLIENT_ID"] = "pre-issued-client"
try:
    oauth._client_cache.clear()
    registered.clear()
    with patch.object(oauth, "discover", AsyncMock(return_value=SHARED)):
        pinned = run(oauth.begin("919000000009", "swiggy_food",
                                 swiggy_food.SwiggyFoodProvider().oauth))
    check("a configured client id still wins",
          parse_qs(urlparse(pinned).query)["client_id"] == ["pre-issued-client"])
    check("and no registration call is made at all", registered == [])
finally:
    os.environ.pop("SWIGGY_OAUTH_CLIENT_ID", None)
    oauth._client_cache.clear()

# ======================================================================
print("\n[10] A restart between authorize and callback must not re-register")
# RFC 6749 4.1.3: the token request identifies the client the code was issued
# to. Registration is cached IN MEMORY, so a restart used to re-register and
# could present a different client to the token endpoint. Swiggy's static
# "swiggy-mcp" masks this; a server issuing per-client ids would fail with an
# opaque token error and no way to tell why.
issued = []


class PerClientRegistration:
    """A server that issues a UNIQUE client id per registration call."""

    status_code = 201

    def json(self):
        issued.append(f"client-{len(issued) + 1}")
        return {"client_id": issued[-1]}


async def _issue(url, json=None, **kw):
    return PerClientRegistration()


def _issuing_client():
    client = AsyncMock()
    client.__aenter__.return_value = client
    client.__aexit__.return_value = False
    client.post = _issue
    return client


issued.clear()
oauth._client_cache.clear()
with patch.object(oauth, "discover", AsyncMock(return_value=SHARED)), \
     patch.object(httpx, "AsyncClient", return_value=_issuing_client()):
    started = run(oauth.begin("919000000010", "swiggy_food",
                              swiggy_food.SwiggyFoodProvider().oauth))

authorized_client = parse_qs(urlparse(started).query)["client_id"][0]
check("the authorization URL names the registered client",
      authorized_client == "client-1")

state_row = db.get_connection().execute(
    "SELECT client_id, code_verifier FROM oauth_states WHERE state = ?",
    (parse_qs(urlparse(started).query)["state"][0],)).fetchone()
check("the client id is PERSISTED with the state, not just in memory",
      state_row[0] == authorized_client)
check("and the PKCE verifier is stored encrypted beside it",
      state_row[1] and state_row[1] != "" and "code" not in str(state_row[1]).lower())

# THE RESTART: process memory is gone, the database is not.
oauth._client_cache.clear()

exchanged = {}


class TokenResponse:
    status_code = 200

    def json(self):
        return {"access_token": "at", "refresh_token": "rt", "expires_in": 432000}


async def _token(url, data=None, headers=None, **kw):
    exchanged.update(data or {})
    return TokenResponse()


token_client = AsyncMock()
token_client.__aenter__.return_value = token_client
token_client.__aexit__.return_value = False
token_client.post = _token

with patch.object(oauth, "discover", AsyncMock(return_value=SHARED)), \
     patch.object(httpx, "AsyncClient", return_value=token_client):
    run(oauth.complete(parse_qs(urlparse(started).query)["state"][0], "auth-code-xyz",
                       lambda name: swiggy_food.SwiggyFoodProvider().oauth))

check("the token exchange names the SAME client the code was issued to",
      exchanged.get("client_id") == authorized_client,
      )
check("no second registration happened across the restart", len(issued) == 1)
check("grant_type is authorization_code", exchanged.get("grant_type") == "authorization_code")
check("the code is sent", exchanged.get("code") == "auth-code-xyz")
check("the redirect_uri is repeated, as the spec requires",
      exchanged.get("redirect_uri") == "http://localhost:8000/link/swiggy_food/callback")
check("the PKCE verifier is sent, and it is NOT the challenge",
      exchanged.get("code_verifier")
      and exchanged["code_verifier"] != parse_qs(urlparse(started).query)["code_challenge"][0])
check("the verifier hashes to the challenge that was published",
      base64.urlsafe_b64encode(
          hashlib.sha256(exchanged["code_verifier"].encode()).digest()
      ).decode().rstrip("=") == parse_qs(urlparse(started).query)["code_challenge"][0])

# The state is spent, so a replayed callback gets nothing.
replayed = None
try:
    with patch.object(oauth, "discover", AsyncMock(return_value=SHARED)):
        run(oauth.complete(parse_qs(urlparse(started).query)["state"][0], "auth-code-xyz",
                           lambda name: swiggy_food.SwiggyFoodProvider().oauth))
except Exception as e:
    replayed = e
check("a replayed callback is refused — state is single-use", replayed is not None)

# The authorization request matches the LIVE metadata document, not our guesses.
LIVE = {
    "response_types_supported": ["code"],
    "code_challenge_methods_supported": ["S256"],
    "scopes_supported": ["mcp:tools", "mcp:resources", "mcp:prompts"],
    "grant_types_supported": ["authorization_code", "refresh_token"],
}
sent = parse_qs(urlparse(started).query)
check("response_type is one the server supports",
      sent["response_type"][0] in LIVE["response_types_supported"])
check("code_challenge_method is one the server supports",
      sent["code_challenge_method"][0] in LIVE["code_challenge_methods_supported"])
check("every scope requested is one the server supports",
      all(s in LIVE["scopes_supported"] for s in sent["scope"][0].split()))
check("the grant we exchange with is supported",
      exchanged["grant_type"] in LIVE["grant_types_supported"])

print(f"RESULT: {_passed} passed, {_failed} failed")
try:
    os.unlink(_tmp.name)
except OSError:
    pass
sys.exit(1 if _failed else 0)
