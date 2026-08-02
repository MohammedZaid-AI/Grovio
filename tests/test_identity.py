"""
Phase 4: user identity, provider account linking, token lifecycle.

No network. Discovery and token exchange are mocked at the HTTP boundary, so
what is exercised is OUR logic: PKCE, state validation, replay prevention,
encryption at rest, refresh, revocation, and conversation continuation.
"""
import asyncio
import os
import pathlib
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from cryptography.fernet import Fernet

# Encryption key BEFORE anything imports crypto.
os.environ["TOKEN_ENCRYPTION_KEY"] = Fernet.generate_key().decode()
os.environ["PUBLIC_BASE_URL"] = "https://concierge.example"
os.environ["WHATSAPP_TRANSPORT"] = "twilio"   # avoid Cloud API config in this suite

import db

_tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
_tmp.close()
db.DB_PATH = _tmp.name
db.init_db()

from ai import identity, skills
from ai.providers import ProviderKind, oauth, registry, vault
from ai.providers.base import Offer
from core import crypto

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


def utc(offset_seconds=0):
    return (datetime.now(timezone.utc) + timedelta(seconds=offset_seconds)).strftime(
        "%Y-%m-%d %H:%M:%S"
    )


METADATA = {
    "authorization_endpoint": "https://provider.example/authorize",
    "token_endpoint": "https://provider.example/token",
}


class LinkableProvider:
    """A provider that requires per-user authorisation. Deliberately not
    Swiggy — the framework must be provider-agnostic."""

    kind = ProviderKind.GROCERY

    def __init__(self, name, display_name):
        self.name = name
        self.display_name = display_name
        self.oauth = oauth.OAuthConfig(
            server_url=f"https://{name}.example/mcp",
            client_id_env=f"{name.upper()}_CLIENT_ID",
        )
        self.seen_tokens = []

    async def search(self, query, ctx):
        self.seen_tokens.append(ctx.access_token)
        return [Offer(provider=self.name, kind=self.kind, id="1", title="Milk", price=50)]


class OpenProvider:
    """Needs no user authorisation."""
    name = "open_provider"
    kind = ProviderKind.RESTAURANT

    async def search(self, query, ctx):
        return [Offer(provider=self.name, kind=self.kind, id="2", title="Pizza")]


def token_payload(access="access-1", refresh="refresh-1", expires_in=3600):
    payload = {"access_token": access, "token_type": "Bearer"}
    if refresh:
        payload["refresh_token"] = refresh
    if expires_in:
        payload["expires_in"] = expires_in
    return payload


ALICE = "919800000001"
BOB = "919800000002"

# ----------------------------------------------------------------------
print("\n[1] Encryption at rest — fails closed")
secret = "super-secret-token"
cipher = crypto.encrypt(secret)
check("ciphertext differs from plaintext", cipher != secret and secret not in cipher)
check("roundtrips", crypto.decrypt(cipher) == secret)
check("None passes through", crypto.encrypt(None) is None and crypto.decrypt(None) is None)

_saved_key = os.environ.pop("TOKEN_ENCRYPTION_KEY")
try:
    crypto.encrypt("x")
    check("no key -> encrypt raises (no plaintext fallback)", False)
except crypto.CryptoNotConfigured:
    check("no key -> encrypt raises (no plaintext fallback)", True)
check("is_configured() reports missing key", crypto.is_configured() is False)
os.environ["TOKEN_ENCRYPTION_KEY"] = _saved_key
check("is_configured() true once set", crypto.is_configured() is True)

os.environ["TOKEN_ENCRYPTION_KEY"] = Fernet.generate_key().decode()
check("wrong key -> decrypt returns None, does not raise", crypto.decrypt(cipher) is None)
os.environ["TOKEN_ENCRYPTION_KEY"] = _saved_key

# ----------------------------------------------------------------------
print("\n[2] User identity — new and existing")
user = identity.load(ALICE)
check("new user created on first contact", user.phone == ALICE)
check("starts in NEW onboarding status", user.onboarding_status == identity.NEW)
check("no providers linked yet", user.linked_providers == [] and not user.is_linked)
check("essentials are known to be missing", set(user.missing_essentials()) == set(identity.ESSENTIAL_FACTS))

created_first = db.get_or_create_user(ALICE)["created_at"]
again = identity.load(ALICE)
check("existing user reused, not duplicated", db.get_or_create_user(ALICE)["created_at"] == created_first)
check("load is idempotent", again.phone == user.phone)

identity.set_display_name(ALICE, "Alice")
check("display name persists", identity.load(ALICE).display_name == "Alice")
check("display name appears in the profile", "Alice" in identity.load(ALICE).describe())

from ai import memory
memory.remember_fact(ALICE, "home", "Indiranagar")
check("essentials shrink as facts are learned", "home" not in identity.load(ALICE).missing_essentials())

# ----------------------------------------------------------------------
print("\n[3] Linking — PKCE, state, encrypted storage")
registry.clear()
groceries = LinkableProvider("acme_grocery", "Acme Groceries")
registry.register(groceries)

with patch.object(oauth, "discover", new=AsyncMock(return_value=METADATA)):
    url = run(oauth.begin(ALICE, groceries.name, groceries.oauth, "get me milk"))

check("authorize URL points at the discovered endpoint", url.startswith(METADATA["authorization_endpoint"]))
check("PKCE challenge sent", "code_challenge=" in url and "code_challenge_method=S256" in url)
check("state included", "state=" in url)
check("redirect_uri uses PUBLIC_BASE_URL", "concierge.example%2Flink%2Facme_grocery%2Fcallback" in url.replace(":", "%3A"))
check("verifier never appears in the URL", "code_verifier" not in url)

state = url.split("state=")[1].split("&")[0]
stored = db.get_connection().execute(
    "SELECT code_verifier, pending_message FROM oauth_states WHERE state = ?", (state,)
).fetchone()
check("PKCE verifier encrypted at rest", stored[0] and crypto.decrypt(stored[0]) and stored[0] != crypto.decrypt(stored[0]))
check("original request stored for resumption", stored[1] == "get me milk")

with patch.object(oauth, "discover", new=AsyncMock(return_value=METADATA)), \
     patch.object(oauth, "_post_token", new=AsyncMock(return_value=token_payload())):
    result = run(oauth.complete(state, "auth-code", registry.oauth_config_for))

check("callback returns the owning phone", result["phone"] == ALICE)
check("callback returns the pending message", result["pending_message"] == "get me milk")

link = db.get_provider_link(ALICE, groceries.name)
check("link marked LINKED", link["status"] == "LINKED")
check("access token stored ENCRYPTED", link["access_token"] != "access-1")
check("access token decrypts correctly", crypto.decrypt(link["access_token"]) == "access-1")
check("refresh token stored ENCRYPTED", crypto.decrypt(link["refresh_token"]) == "refresh-1")
check("expiry recorded", link["expires_at"] is not None)

identity.mark_linked(ALICE)
check("onboarding advances to LINKED", identity.load(ALICE).onboarding_status == identity.LINKED)
check("provider shows as linked", identity.load(ALICE).has_linked(groceries.name))

# ----------------------------------------------------------------------
print("\n[4] Invalid callbacks are rejected, nothing mutated")


def expect_oauth_error(label, state_value, code="c"):
    try:
        with patch.object(oauth, "discover", new=AsyncMock(return_value=METADATA)), \
             patch.object(oauth, "_post_token", new=AsyncMock(return_value=token_payload())):
            run(oauth.complete(state_value, code, registry.oauth_config_for))
        check(label, False)
    except oauth.OAuthError:
        check(label, True)


expect_oauth_error("unknown state rejected", "not-a-real-state")
expect_oauth_error("REPLAYED state rejected (single-use)", state)
expect_oauth_error("missing code rejected", "whatever", code="")
expect_oauth_error("missing state rejected", "", code="c")

expired_state = "expired-state-token"
db.save_oauth_state(expired_state, ALICE, groceries.name, crypto.encrypt("v"), "hi", utc(-60))
expect_oauth_error("expired state rejected", expired_state)

db.save_oauth_state("orphan-state", ALICE, "ghost_provider", crypto.encrypt("v"), "hi", utc(600))
expect_oauth_error("unknown provider rejected", "orphan-state")

# ----------------------------------------------------------------------
print("\n[5] Token lifecycle — valid, expired, revoked")
with patch.object(oauth, "discover", new=AsyncMock(return_value=METADATA)):
    token = run(vault.access_token(ALICE, groceries.name, groceries.oauth))
check("valid token returned as plaintext to the vault caller", token == "access-1")

# Expired -> refreshed transparently.
db.save_provider_link(ALICE, groceries.name, crypto.encrypt("old"), crypto.encrypt("refresh-1"), utc(-10))
with patch.object(oauth, "discover", new=AsyncMock(return_value=METADATA)), \
     patch.object(oauth, "_post_token", new=AsyncMock(return_value=token_payload(access="access-2"))):
    token = run(vault.access_token(ALICE, groceries.name, groceries.oauth))
check("expired token refreshed silently", token == "access-2")
check("still LINKED after refresh", db.get_provider_link(ALICE, groceries.name)["status"] == "LINKED")

# Provider rotates nothing -> old refresh token retained.
db.save_provider_link(ALICE, groceries.name, crypto.encrypt("old"), crypto.encrypt("refresh-keep"), utc(-10))
with patch.object(oauth, "discover", new=AsyncMock(return_value=METADATA)), \
     patch.object(oauth, "_post_token", new=AsyncMock(return_value=token_payload(access="a3", refresh=None))):
    run(vault.access_token(ALICE, groceries.name, groceries.oauth))
check("refresh token retained when provider doesn't rotate it",
      crypto.decrypt(db.get_provider_link(ALICE, groceries.name)["refresh_token"]) == "refresh-keep")

# Refresh rejected (user revoked at the provider) -> REVOKED + NeedsLink.
db.save_provider_link(ALICE, groceries.name, crypto.encrypt("old"), crypto.encrypt("bad"), utc(-10))
try:
    with patch.object(oauth, "discover", new=AsyncMock(return_value=METADATA)), \
         patch.object(oauth, "_post_token", new=AsyncMock(side_effect=oauth.OAuthError("invalid_grant"))):
        run(vault.access_token(ALICE, groceries.name, groceries.oauth))
    check("revoked grant raises NeedsLink", False)
except vault.NeedsLink as e:
    check("revoked grant raises NeedsLink", e.reason == "revoked")
check("link marked REVOKED after refresh rejection",
      db.get_provider_link(ALICE, groceries.name)["status"] == "REVOKED")
check("revoked link no longer reported as linked", not vault.is_linked(ALICE, groceries.name))

# Expired with no refresh token at all.
db.save_provider_link(ALICE, groceries.name, crypto.encrypt("x"), None, utc(-10))
try:
    run(vault.access_token(ALICE, groceries.name, groceries.oauth))
    check("expired with no refresh token -> NeedsLink", False)
except vault.NeedsLink as e:
    check("expired with no refresh token -> NeedsLink", e.reason == "expired")

# Never linked at all.
try:
    run(vault.access_token(BOB, groceries.name, groceries.oauth))
    check("unlinked user -> NeedsLink", False)
except vault.NeedsLink as e:
    check("unlinked user -> NeedsLink", e.reason == "not_linked")

# No expiry recorded == does not expire.
db.save_provider_link(ALICE, groceries.name, crypto.encrypt("forever"), None, None)
check("token with no expiry is used as-is",
      run(vault.access_token(ALICE, groceries.name, groceries.oauth)) == "forever")

# ----------------------------------------------------------------------
print("\n[6] Duplicate linking and multiple providers")
before = db.get_connection().execute(
    "SELECT COUNT(*) FROM provider_links WHERE phone = ?", (ALICE,)
).fetchone()[0]
db.save_provider_link(ALICE, groceries.name, crypto.encrypt("newest"), crypto.encrypt("r"), utc(3600))
after = db.get_connection().execute(
    "SELECT COUNT(*) FROM provider_links WHERE phone = ?", (ALICE,)
).fetchone()[0]
check("re-linking updates in place, never a second row", before == after)
check("latest tokens win", crypto.decrypt(db.get_provider_link(ALICE, groceries.name)["access_token"]) == "newest")

meals = LinkableProvider("bistro_meals", "Bistro")
registry.register(meals)
db.save_provider_link(ALICE, meals.name, crypto.encrypt("meals-token"), None, None)
check("two providers linked independently", set(db.get_linked_providers(ALICE)) == {groceries.name, meals.name})

vault._revoke(ALICE, meals.name, "test")
check("revoking one provider leaves the other LINKED", db.get_linked_providers(ALICE) == [groceries.name])

vault.unlink(ALICE, groceries.name)
check("unlink removes the row entirely", db.get_provider_link(ALICE, groceries.name) is None)
check("unlinked user has no providers", db.get_linked_providers(ALICE) == [])

# ----------------------------------------------------------------------
print("\n[7] Skills surface link prompts, never credentials")
registry.clear()
registry.register(groceries)

alice = identity.load(ALICE)
with patch.object(oauth, "discover", new=AsyncMock(return_value=METADATA)):
    result = run(skills.find_food(alice, "milk", "grocery", pending_message="I need milk"))

check("unlinked user gets NEEDS_LINK", result.status == skills.SkillStatus.NEEDS_LINK)
check("a link URL is provided", result.link_url and result.link_url.startswith("https://"))
check("provider display name used, not the internal name", result.provider_label == "Acme Groceries")
check("model instructed not to invent options", "do not invent" in result.message.lower())
check("no token in the skill result", "access" not in (result.message or "").lower().replace("access your", ""))
check("original request stored for resumption",
      db.get_connection().execute(
          "SELECT pending_message FROM oauth_states ORDER BY rowid DESC LIMIT 1"
      ).fetchone()[0] == "I need milk")

# Linked user reaches the provider, and the provider receives the token.
db.save_provider_link(ALICE, groceries.name, crypto.encrypt("live-token"), None, None)
groceries.seen_tokens.clear()
result = run(skills.find_food(identity.load(ALICE), "milk", "grocery"))
check("linked user gets real results", result.status == skills.SkillStatus.OK)
check("provider received the decrypted token", groceries.seen_tokens == ["live-token"])

# A provider needing no authorisation is unaffected.
registry.register(OpenProvider())
result = run(skills.find_food(identity.load(BOB), "pizza", "restaurant"))
check("provider needing no link works for an unlinked user", result.status == skills.SkillStatus.OK)

with patch.object(oauth, "discover", new=AsyncMock(return_value=METADATA)):
    disconnect = skills.disconnect_provider(identity.load(ALICE), "Acme")
check("disconnect by friendly name works", disconnect.ok)
check("disconnect actually removed credentials", db.get_provider_link(ALICE, groceries.name) is None)

# ----------------------------------------------------------------------
print("\n[8] Callback route + conversation continuation")
os.environ["TWILIO_AUTH_TOKEN"] = "t" * 32
from fastapi.testclient import TestClient
from backend.app import app

registry.clear()
registry.register(groceries)

with TestClient(app) as client:
    with patch.object(oauth, "discover", new=AsyncMock(return_value=METADATA)):
        link_url = run(oauth.begin(BOB, groceries.name, groceries.oauth, "order me bread"))
    bob_state = link_url.split("state=")[1].split("&")[0]

    with patch.object(oauth, "discover", new=AsyncMock(return_value=METADATA)), \
         patch.object(oauth, "_post_token", new=AsyncMock(return_value=token_payload(access="bob-token"))):
        response = client.get(
            f"/link/{groceries.name}/callback", params={"state": bob_state, "code": "xyz"}
        )

    check("callback returns a success page", response.status_code == 200)
    check("page confirms the provider by display name", "Acme Groceries" in response.text)
    check("no token echoed to the browser", "bob-token" not in response.text)
    check("tokens stored for the right user", crypto.decrypt(db.get_provider_link(BOB, groceries.name)["access_token"]) == "bob-token")

    queued = db.get_connection().execute(
        "SELECT body FROM whatsapp_inbound WHERE phone = ?", (BOB,)
    ).fetchall()
    check("CONVERSATION CONTINUES: original request re-queued", [r[0] for r in queued] == ["order me bread"])

    bad = client.get(f"/link/{groceries.name}/callback", params={"state": "nope", "code": "x"})
    check("invalid callback -> 400", bad.status_code == 400)
    check("invalid callback stays vague (no internals leaked)",
          "state" not in bad.text.lower() and "oauth" not in bad.text.lower())

    replay = client.get(f"/link/{groceries.name}/callback", params={"state": bob_state, "code": "xyz"})
    check("replayed callback -> 400", replay.status_code == 400)

    denied = client.get(f"/link/{groceries.name}/callback", params={"error": "access_denied"})
    check("user refusal handled gracefully", denied.status_code == 400 and "WhatsApp" in denied.text)

# ----------------------------------------------------------------------
print("\n[9] Discovery is standards-based, not invented")
oauth.clear_discovery_cache()
calls = []


async def fake_fetch(client, url):
    calls.append(url)
    if url.endswith("/.well-known/oauth-protected-resource"):
        return {"authorization_servers": ["https://auth.example"]}
    if url == "https://auth.example/.well-known/oauth-authorization-server":
        return METADATA
    return None


with patch.object(oauth, "_fetch_json", new=fake_fetch):
    found = run(oauth.discover(oauth.OAuthConfig(server_url="https://res.example/mcp")))
check("RFC 9728 protected-resource document consulted",
      any(u.endswith("/.well-known/oauth-protected-resource") for u in calls))
check("RFC 8414 authorization-server metadata consulted",
      "https://auth.example/.well-known/oauth-authorization-server" in calls)
check("endpoints resolved from discovery", found["token_endpoint"] == METADATA["token_endpoint"])

oauth.clear_discovery_cache()
explicit = run(oauth.discover(oauth.OAuthConfig(
    server_url="https://x.example",
    authorize_url="https://x.example/a",
    token_url="https://x.example/t",
)))
check("explicit endpoints bypass discovery", explicit["authorization_endpoint"] == "https://x.example/a")

oauth.clear_discovery_cache()
try:
    with patch.object(oauth, "_fetch_json", new=AsyncMock(return_value=None)):
        run(oauth.discover(oauth.OAuthConfig(server_url="https://silent.example")))
    check("undiscoverable provider raises a clear error", False)
except oauth.OAuthError as e:
    check("undiscoverable provider raises a clear error", "discover" in str(e).lower())

# ----------------------------------------------------------------------
print("\n[10] ARCHITECTURE: the planner never learns OAuth exists")
root = pathlib.Path(__file__).resolve().parent.parent


def code_identifiers(path):
    """Identifiers in real CODE — comments and string literals removed.

    Docstrings may legitimately name a platform as an example, and the system
    prompt legitimately tells the model never to ask for credentials. Neither is
    a coupling violation. What matters is whether the module can actually TOUCH
    these things, so scan executable tokens only.
    """
    import io
    import tokenize

    names = set()
    with open(path, "rb") as fh:
        for token in tokenize.tokenize(io.BytesIO(fh.read()).readline):
            if token.type == tokenize.NAME:
                names.add(token.string.lower())
    return names


FORBIDDEN = ("oauth", "vault", "access_token", "refresh_token", "crypto",
             "encrypt", "decrypt", "swiggy", "swiggyinstamart")

planner_names = code_identifiers(root / "ai" / "planner.py")
leaks = sorted(planner_names & set(FORBIDDEN))
check(f"ai/planner.py cannot touch credentials {leaks or ''}", not leaks)

concierge_names = code_identifiers(root / "ai" / "concierge.py")
leaks = sorted(concierge_names & set(FORBIDDEN))
check(f"ai/concierge.py cannot touch credentials {leaks or ''}", not leaks)

# Skills MAY start a link (they call oauth.begin) but must never read a token.
skills_names = code_identifiers(root / "ai" / "skills.py")
leaks = sorted(skills_names & {"access_token", "refresh_token", "decrypt", "swiggy"})
check(f"ai/skills.py never reads a credential {leaks or ''}", not leaks)

oauth_names = code_identifiers(root / "ai" / "providers" / "oauth.py")
check("the OAuth engine references no platform in code",
      not (oauth_names & {"swiggy", "swiggyinstamart", "instamart"}))

vault_names = code_identifiers(root / "ai" / "providers" / "vault.py")
check("the vault references no platform in code",
      not (vault_names & {"swiggy", "swiggyinstamart", "instamart"}))

print("\n" + "=" * 70)
print(f"RESULT: {_passed} passed, {_failed} failed")
try:
    os.unlink(_tmp.name)
except OSError:
    pass
sys.exit(1 if _failed else 0)
