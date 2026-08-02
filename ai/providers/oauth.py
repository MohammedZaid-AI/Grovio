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
from urllib.parse import urlencode

import httpx

import db
from core.crypto import decrypt, encrypt
from core.logger import logger

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


@dataclass(frozen=True)
class OAuthConfig:
    """What a provider must declare to become linkable."""
    server_url: str                      # the resource (MCP) server
    scopes: tuple = ()
    client_id_env: str | None = None     # env var holding a pre-issued client id
    client_secret_env: str | None = None
    authorize_url: str | None = None     # set only if discovery is unavailable
    token_url: str | None = None

    @property
    def client_id(self) -> str | None:
        return os.getenv(self.client_id_env, "").strip() or None if self.client_id_env else None

    @property
    def client_secret(self) -> str | None:
        return os.getenv(self.client_secret_env, "").strip() or None if self.client_secret_env else None


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
async def _fetch_json(client, url):
    try:
        response = await client.get(url)
        if response.status_code == 200:
            return response.json()
    except Exception as e:
        logger.info(f"[oauth] discovery miss {url}: {type(e).__name__}")
    return None


async def _challenge_resource_metadata(client, server_url: str):
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
        logger.info(f"[oauth] challenge probe failed: {type(e).__name__}")
        return None

    if response.status_code not in (401, 403):
        return None

    header = response.headers.get("www-authenticate", "")
    match = re.search(r'resource_metadata="?([^",\s]+)"?', header)
    if not match:
        logger.info(f"[oauth] {response.status_code} carried no resource_metadata hint")
        return None
    return match.group(1)


async def discover(config: OAuthConfig) -> dict:
    """Resolve authorize/token endpoints. Cached per server URL.

    Order: explicit configuration, then the server's own 401 challenge, then
    well-known probing. The challenge is authoritative; the probes are a
    fallback for servers that don't implement it.
    """
    if config.authorize_url and config.token_url:
        return {
            "authorization_endpoint": config.authorize_url,
            "token_endpoint": config.token_url,
        }

    cached = _metadata_cache.get(config.server_url)
    if cached:
        return cached

    server = config.server_url.rstrip("/")
    async with httpx.AsyncClient(timeout=DISCOVERY_TIMEOUT, follow_redirects=True) as client:
        # RFC 9728 protected-resource metadata, however we can reach it.
        resource_meta = await _fetch_json(client, f"{server}/.well-known/oauth-protected-resource")

        if not resource_meta:
            hinted = await _challenge_resource_metadata(client, server)
            if hinted:
                logger.info("[oauth] discovered resource metadata via 401 challenge")
                resource_meta = await _fetch_json(client, hinted)

        issuers = (resource_meta or {}).get("authorization_servers") or []
        candidates = [issuer.rstrip("/") for issuer in issuers] or [server]

        for issuer in candidates:
            # RFC 8414, plus the OIDC-style path some servers use instead.
            for path in ("/.well-known/oauth-authorization-server",
                         "/.well-known/openid-configuration"):
                metadata = await _fetch_json(client, f"{issuer}{path}")
                if metadata and metadata.get("authorization_endpoint") and metadata.get("token_endpoint"):
                    _metadata_cache[config.server_url] = metadata
                    return metadata

    raise OAuthError(
        f"Could not discover OAuth endpoints for {config.server_url}. The provider "
        f"publishes no RFC 8414/9728 metadata and returned no 401 challenge — "
        f"declare authorize_url and token_url on its OAuthConfig instead."
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

    async with httpx.AsyncClient(timeout=TOKEN_TIMEOUT) as client:
        response = await client.post(
            metadata["token_endpoint"],
            data=form,
            headers={"Accept": "application/json"},
        )

    if response.status_code != 200:
        # Never surface a provider body to a user; log for us, raise generically.
        logger.error(f"[oauth] token endpoint returned {response.status_code}")
        raise OAuthError(f"token exchange failed (HTTP {response.status_code})")

    payload = response.json()
    if not payload.get("access_token"):
        raise OAuthError("token response contained no access_token")
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
