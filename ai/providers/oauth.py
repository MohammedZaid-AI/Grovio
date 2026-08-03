"""
Provider-agnostic OAuth 2.1 + PKCE.

Nothing here is specific to any platform. A provider contributes a server URL
and, optionally, a pre-issued client id; it contributes no protocol. The same
code links Swiggy today and ONDC later.

Endpoint discovery follows IETF standards, not vendor paths:
  * RFC 9728 — /.well-known/oauth-protected-resource on the resource server
  * RFC 8414 — /.well-known/oauth-authorization-server on the auth server

That matters: it means we are not inventing anybody's API. If a provider does
not serve these documents, it declares its endpoints explicitly instead
(`OAuthConfig.authorize_url` / `token_url`) and the rest of the flow is
unchanged.

SECURITY
  * PKCE S256 — a leaked authorization code is useless without the verifier.
  * State is 256 bits of `secrets` entropy, bound to phone + provider.
  * State is SINGLE-USE via an atomic DB claim — that is the replay guard.
  * States expire in 10 minutes.
  * Verifiers are encrypted at rest, like tokens.
"""
import base64
import hashlib
import os
import re
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode, urlsplit

import httpx

import db
from core.crypto import decrypt, encrypt
from core.logger import logger

from ai.providers import failures
from ai.providers.failures import Failure

STATE_TTL_MINUTES = 10
DISCOVERY_TIMEOUT = 10
TOKEN_TIMEOUT = 20

_metadata_cache: dict = {}
_client_cache: dict = {}

# Sent during dynamic client registration; this is how we appear in a user's
# consent screen and in the provider's dashboard.
CLIENT_NAME = os.getenv("OAUTH_CLIENT_NAME", "AI Food Concierge")


class OAuthError(Exception):
    """Linking could not be completed. The message is safe to log, never to
    show a user verbatim."""
    failure = Failure.UNKNOWN


class OAuthNotConfigured(OAuthError):
    """We do not know where this provider's OAuth endpoints are.

    OUR problem, not theirs. The server answered fine — it simply does not
    publish metadata where we looked, and no explicit endpoints were declared.
    Retrying changes nothing, so the user must never be told to try again.
    """
    failure = Failure.CONFIGURATION


class OAuthUnreachable(OAuthError):
    """The provider could not be reached, or returned a server error.

    The one case where "temporarily unavailable, try shortly" is honest.
    """
    failure = Failure.UNAVAILABLE


@dataclass(frozen=True)
class OAuthConfig:
    """What a provider must declare to become linkable."""
    server_url: str                      # the resource (MCP) server
    scopes: tuple = ()
    client_id_env: str | None = None     # env var holding a pre-issued client id
    client_secret_env: str | None = None

    # Declared endpoints, used when discovery finds nothing. A provider fills
    # these in from its VENDOR DOCUMENTATION — never from a guess — so that a
    # server which publishes no metadata is a configuration detail, not an
    # outage. Discovery still runs first, so a provider that moves its
    # endpoints keeps working without a code change.
    authorize_url: str | None = None
    token_url: str | None = None
    # Carried through the fallback because a provider that issues client ids by
    # dynamic registration cannot be linked without it: the authorization
    # request would go out with no client_id and be rejected. Omitting this is
    # a fallback that only appears to work.
    registration_url: str | None = None

    # Operator overrides. Set these to pin endpoints without touching code;
    # they beat both discovery and the declared defaults.
    authorize_url_env: str | None = None
    token_url_env: str | None = None

    @staticmethod
    def _env(name: str | None) -> str | None:
        return os.getenv(name, "").strip() or None if name else None

    @property
    def client_id(self) -> str | None:
        return self._env(self.client_id_env)

    @property
    def client_secret(self) -> str | None:
        return self._env(self.client_secret_env)

    def _metadata(self, authorize: str, token: str) -> dict:
        metadata = {"authorization_endpoint": authorize, "token_endpoint": token}
        if self.registration_url:
            metadata["registration_endpoint"] = self.registration_url
        return metadata

    @property
    def override(self) -> dict | None:
        """Operator-pinned endpoints, if both were supplied."""
        authorize = self._env(self.authorize_url_env)
        token = self._env(self.token_url_env)
        return self._metadata(authorize, token) if authorize and token else None

    @property
    def declared(self) -> dict | None:
        """The provider's documented endpoints, if it declared both."""
        if self.authorize_url and self.token_url:
            return self._metadata(self.authorize_url, self.token_url)
        return None


def redirect_uri(provider: str) -> str:
    """Where the provider sends the user back.

    This exact URL must be registered with the provider — for Swiggy that is
    what Builders Club approval grants (see FEASIBILITY.md).
    """
    base = os.getenv("PUBLIC_BASE_URL", "http://localhost:8000").rstrip("/")
    return f"{base}/link/{provider}/callback"


# ----------------------------------------------------------------------
# Discovery
# ----------------------------------------------------------------------
class Trail(list):
    """The discovery attempt log.

    Also records whether the server answered AT ALL. That is the difference
    between "they are down" (nothing responded) and "they publish no metadata
    where we looked" (404s — our configuration problem, not their outage), and
    the two must never be reported to a user the same way.
    """
    responded = False


async def _fetch_json(client, url, trail: Trail):
    """GET a metadata document, recording exactly what happened.

    Every attempt lands in `trail`, so a discovery failure can be explained —
    which URL, and why — instead of just "could not discover".
    """
    try:
        response = await client.get(url)
    except httpx.TimeoutException:
        trail.append(f"{url} -> TIMEOUT")
        return None
    except Exception as e:
        trail.append(f"{url} -> {type(e).__name__}")
        return None

    trail.responded = True      # they are up; the document just isn't here
    if response.status_code != 200:
        trail.append(f"{url} -> HTTP {response.status_code}")
        return None

    try:
        payload = response.json()
    except Exception:
        trail.append(f"{url} -> HTTP 200 but the body is not JSON")
        return None

    trail.append(f"{url} -> HTTP 200 ✓")
    return payload


def _metadata_urls(url: str, document: str) -> list:
    """Every place a server might publish `document`, best first.

    RFC 8414 §3.1 and RFC 9728 §3.1 both say the well-known segment goes
    BETWEEN the host and the path — `https://host/.well-known/doc/im` — not
    appended to the end of it. Servers also commonly publish at the origin root.

    Probing only the appended form is what broke Swiggy linking: they serve
    `https://mcp.swiggy.com/.well-known/oauth-authorization-server` (the root),
    and 404 the `https://mcp.swiggy.com/im/.well-known/...` form we asked for.
    """
    parsed = urlsplit(url)
    root = f"{parsed.scheme}://{parsed.netloc}"
    path = parsed.path.rstrip("/")

    candidates = []
    if path:
        candidates.append(f"{root}/.well-known/{document}{path}")   # RFC form
        candidates.append(f"{root}{path}/.well-known/{document}")   # appended
    candidates.append(f"{root}/.well-known/{document}")             # origin root
    return list(dict.fromkeys(candidates))


async def _challenge_resource_metadata(client, server_url: str, trail: list):
    """Ask the MCP server itself where its authorization server lives.

    The MCP authorization spec says an unauthenticated request is answered with
    401 and a `WWW-Authenticate: Bearer resource_metadata="<url>"` header. This
    is the authoritative path — guessing well-known URLs only works when the
    resource server happens to host them itself.
    """
    try:
        response = await client.post(
            server_url,
            json={"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
            headers={"Accept": "application/json, text/event-stream"},
        )
    except Exception as e:
        trail.append(f"401-challenge {server_url} -> {type(e).__name__}")
        return None

    if response.status_code not in (401, 403):
        trail.append(f"401-challenge {server_url} -> HTTP {response.status_code}, no challenge")
        return None

    header = response.headers.get("www-authenticate", "")
    match = re.search(r'resource_metadata="?([^",\s]+)"?', header)
    if not match:
        trail.append(f"401-challenge {server_url} -> {response.status_code} with no "
                     f"resource_metadata hint")
        return None
    trail.append(f"401-challenge {server_url} -> hint {match.group(1)} ✓")
    return match.group(1)


async def discover(config: OAuthConfig) -> dict:
    """Resolve authorize/token endpoints. Cached per server URL.

    Four tiers, most authoritative first:

      1. operator override  — env vars, a deliberate pin
      2. discovery          — RFC 9728 resource metadata, the 401 challenge,
                              then RFC 8414 / OIDC metadata
      3. declared defaults  — the provider's documented endpoints
      4. OAuthNotConfigured — a CONFIGURATION error, never an outage

    Discovery outranks the declared defaults so a provider that moves its
    endpoints keeps working with no code change; the defaults outrank failing,
    so a provider that publishes nothing still links.
    """
    override = config.override
    if override:
        logger.info(f"[oauth] using operator-pinned endpoints for {config.server_url}")
        return override

    cached = _metadata_cache.get(config.server_url)
    if cached:
        return cached

    server = config.server_url.rstrip("/")
    trail = Trail()

    async with httpx.AsyncClient(timeout=DISCOVERY_TIMEOUT, follow_redirects=True) as client:
        # RFC 9728 protected-resource metadata, however we can reach it.
        resource_meta = None
        for url in _metadata_urls(server, "oauth-protected-resource"):
            resource_meta = await _fetch_json(client, url, trail)
            if resource_meta:
                break

        if not resource_meta:
            hinted = await _challenge_resource_metadata(client, server, trail)
            if hinted:
                resource_meta = await _fetch_json(client, hinted, trail)

        # Where the authorization server lives. The resource document names it;
        # failing that, the resource server's own origin is the usual answer.
        issuers = [i.rstrip("/") for i in (resource_meta or {}).get("authorization_servers") or []]
        if resource_meta and not issuers:
            trail.append("resource metadata found but lists no authorization_servers")

        for issuer in issuers or [server]:
            for document in ("oauth-authorization-server", "openid-configuration"):
                for url in _metadata_urls(issuer, document):
                    metadata = await _fetch_json(client, url, trail)
                    if metadata and metadata.get("authorization_endpoint") \
                            and metadata.get("token_endpoint"):
                        logger.info(f"[oauth] discovered endpoints for {config.server_url} at {url}")
                        _metadata_cache[config.server_url] = metadata
                        return metadata

    # Discovery found nothing. Say exactly what was tried before falling back —
    # "could not discover" alone is unactionable.
    logger.warning(
        f"[oauth] discovery FAILED for {config.server_url}. Attempts:\n  "
        + "\n  ".join(trail or ["(none)"])
    )

    declared = config.declared
    if declared:
        logger.warning(
            f"[oauth] falling back to the endpoints declared by the provider: "
            f"authorize={declared['authorization_endpoint']} "
            f"token={declared['token_endpoint']} — fallback SUCCEEDED, linking "
            f"can proceed. Discovery is preferred; check the trail above."
        )
        _metadata_cache[config.server_url] = declared
        return declared

    logger.error(
        f"[oauth] no fallback endpoints declared for {config.server_url} — "
        f"fallback UNAVAILABLE. Set authorize_url/token_url on its OAuthConfig "
        f"from the vendor's documentation, or pin them via env."
    )
    if not trail.responded:
        # Nothing answered at all. That IS an outage, and reporting it as our
        # misconfiguration would be as wrong as the reverse.
        raise OAuthUnreachable(
            f"{config.server_url} never responded. Tried: {'; '.join(trail)}"
        )

    # They answered — 404s, mostly. The server is up; we simply do not know
    # where its OAuth endpoints are. That is configuration, not an outage.
    raise OAuthNotConfigured(
        f"No OAuth endpoints for {config.server_url}: the server responded but "
        f"publishes no metadata we could find, and declares no fallback. "
        f"Tried: {'; '.join(trail) or '(nothing)'}"
    )


def clear_discovery_cache():
    _metadata_cache.clear()
    _client_cache.clear()


# ----------------------------------------------------------------------
# Flow
# ----------------------------------------------------------------------
async def _register_client(metadata: dict, provider: str) -> str | None:
    """Dynamic client registration (RFC 7591).

    Without a client_id the authorization request is rejected, and providers
    only issue one manually as part of an approval process. Servers that support
    DCR let us self-register, which is what makes linking work before any
    paperwork exists.

    Cached in memory: a restart re-registers, which is harmless but wasteful.
    Persist it if registration ever becomes rate-limited.
    """
    endpoint = metadata.get("registration_endpoint")
    if not endpoint:
        return None

    cached = _client_cache.get(endpoint)
    if cached:
        return cached

    try:
        async with httpx.AsyncClient(timeout=TOKEN_TIMEOUT) as client:
            response = await client.post(endpoint, json={
                "client_name": CLIENT_NAME,
                "redirect_uris": [redirect_uri(provider)],
                "grant_types": ["authorization_code", "refresh_token"],
                "response_types": ["code"],
                "token_endpoint_auth_method": "none",   # public client + PKCE
            })
    except Exception as e:
        logger.info(f"[oauth] dynamic registration failed: {type(e).__name__}")
        return None

    if response.status_code not in (200, 201):
        logger.info(f"[oauth] dynamic registration rejected (HTTP {response.status_code})")
        return None

    client_id = (response.json() or {}).get("client_id")
    if client_id:
        _client_cache[endpoint] = client_id
        logger.info(f"[oauth] registered dynamically with {provider}")
    return client_id


async def _resolve_client_id(config: OAuthConfig, metadata: dict, provider: str) -> str | None:
    """A configured client id always wins; otherwise try to self-register."""
    return config.client_id or await _register_client(metadata, provider)


def _pkce():
    verifier = secrets.token_urlsafe(64)[:128]
    digest = hashlib.sha256(verifier.encode()).digest()
    challenge = base64.urlsafe_b64encode(digest).decode().rstrip("=")
    return verifier, challenge


async def begin(phone: str, provider: str, config: OAuthConfig,
                pending_message: str | None = None) -> str:
    """Start a link. Returns the URL to send the user.

    `pending_message` is what they asked for — stored so the conversation can
    resume afterwards without them repeating themselves.
    """
    metadata = await discover(config)

    verifier, challenge = _pkce()
    state = secrets.token_urlsafe(32)

    db.save_oauth_state(
        state=state,
        phone=phone,
        provider=provider,
        code_verifier=encrypt(verifier),
        pending_message=pending_message,
        expires_at=(datetime.now(timezone.utc) + timedelta(minutes=STATE_TTL_MINUTES))
        .strftime("%Y-%m-%d %H:%M:%S"),
    )

    params = {
        "response_type": "code",
        "redirect_uri": redirect_uri(provider),
        "state": state,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
    }
    client_id = await _resolve_client_id(config, metadata, provider)
    if client_id:
        params["client_id"] = client_id
    if config.scopes:
        params["scope"] = " ".join(config.scopes)

    logger.info(f"[oauth] link started provider={provider} state={state[:8]}…")
    return f"{metadata['authorization_endpoint']}?{urlencode(params)}"


def _expiry_from(payload: dict):
    expires_in = payload.get("expires_in")
    if not expires_in:
        return None
    try:
        seconds = int(expires_in)
    except (TypeError, ValueError):
        return None
    return (datetime.now(timezone.utc) + timedelta(seconds=seconds)).strftime("%Y-%m-%d %H:%M:%S")


async def _post_token(metadata: dict, config: OAuthConfig, form: dict,
                      client_id: str | None = None) -> dict:
    client_id = client_id or config.client_id
    if client_id:
        form["client_id"] = client_id
    if config.client_secret:
        form["client_secret"] = config.client_secret

    try:
        async with httpx.AsyncClient(timeout=TOKEN_TIMEOUT) as client:
            response = await client.post(
                metadata["token_endpoint"],
                data=form,
                headers={"Accept": "application/json"},
            )
    except Exception as e:
        logger.error(f"[oauth] token endpoint unreachable: {type(e).__name__}")
        raise OAuthUnreachable(f"token endpoint unreachable: {type(e).__name__}") from e

    if response.status_code != 200:
        # Never surface a provider body to a user; log for us, raise classified.
        logger.error(
            f"[oauth] token endpoint {metadata['token_endpoint']} returned "
            f"{response.status_code}: {response.text[:300]}"
        )
        error = OAuthError(f"token exchange failed (HTTP {response.status_code})")
        # A 4xx here is our request being wrong; a 5xx is theirs being broken.
        error.failure = failures.from_status(response.status_code)
        raise error

    try:
        payload = response.json()
    except Exception as e:
        error = OAuthError("token response was not JSON")
        error.failure = Failure.PARSING
        raise error from e

    if not payload.get("access_token"):
        error = OAuthError("token response contained no access_token")
        error.failure = Failure.PARSING
        raise error
    return payload


async def complete(state: str, code: str, config_for) -> dict:
    """Finish a link from the callback.

    `config_for(provider)` resolves the provider's OAuthConfig — passed in so
    this module never imports a provider.

    Returns {phone, provider, pending_message}. Raises OAuthError on any invalid
    callback, having mutated nothing the caller can act on.
    """
    if not state or not code:
        raise OAuthError("callback missing state or code")

    # Atomic single-use claim — the replay guard.
    row = db.claim_oauth_state(state)
    if not row:
        raise OAuthError("unknown, expired or already-used state")

    expires_at = row["expires_at"]
    if expires_at and str(expires_at) < datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"):
        raise OAuthError("link expired")

    provider = row["provider"]
    config = config_for(provider)
    if config is None:
        raise OAuthError(f"unknown provider {provider!r}")

    verifier = decrypt(row["code_verifier"])
    if not verifier:
        raise OAuthError("stored PKCE verifier could not be read")

    metadata = await discover(config)
    client_id = await _resolve_client_id(config, metadata, provider)
    payload = await _post_token(metadata, config, {
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": redirect_uri(provider),
        "code_verifier": verifier,
    }, client_id=client_id)

    db.save_provider_link(
        phone=row["phone"],
        provider=provider,
        access_token=encrypt(payload["access_token"]),
        refresh_token=encrypt(payload.get("refresh_token")),
        expires_at=_expiry_from(payload),
        scope=payload.get("scope"),
        client_id=client_id,
    )

    logger.info(f"[oauth] link completed provider={provider}")
    return {
        "phone": row["phone"],
        "provider": provider,
        "pending_message": row["pending_message"],
    }


async def refresh(phone: str, provider: str, config: OAuthConfig, refresh_token: str) -> bool:
    """Exchange a refresh token for a new access token. False if the provider
    rejected it, which means the user must re-link."""
    try:
        metadata = await discover(config)
        client_id = await _resolve_client_id(config, metadata, provider)
        payload = await _post_token(metadata, config, {
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
        }, client_id=client_id)
    except OAuthError as e:
        logger.info(f"[oauth] refresh failed provider={provider}: {e}")
        return False

    db.save_provider_link(
        phone=phone,
        provider=provider,
        access_token=encrypt(payload["access_token"]),
        # Providers may or may not rotate the refresh token; keep the old one
        # when they don't, or the next refresh has nothing to present.
        refresh_token=encrypt(payload.get("refresh_token") or refresh_token),
        expires_at=_expiry_from(payload),
        scope=payload.get("scope"),
        client_id=config.client_id,
    )
    return True
