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
import asyncio
import os
import sys
import tempfile
from unittest.mock import AsyncMock, patch

sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import httpx
from cryptography.fernet import Fernet

os.environ["TOKEN_ENCRYPTION_KEY"] = Fernet.generate_key().decode()
os.environ["WHATSAPP_TRANSPORT"] = "cloud"
# Importing the provider modules pulls in mcp_use, which otherwise tries to
# post telemetry and stalls the suite on SSL retries.
os.environ["MCP_USE_ANONYMIZED_TELEMETRY"] = "false"

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
print(f"RESULT: {_passed} passed, {_failed} failed")
try:
    os.unlink(_tmp.name)
except OSError:
    pass
sys.exit(1 if _failed else 0)
