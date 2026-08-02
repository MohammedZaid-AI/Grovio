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


async def discover(config: OAuthConfig) -> dict:
    """Resolve authorize/token endpoints. Cached per server URL.

    Explicit configuration wins; otherwise walk the two standard documents.
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
        # RFC 9728: the resource server names its authorization server.
        resource_meta = await _fetch_json(client, f"{server}/.well-known/oauth-protected-resource")
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
        f"may not publish RFC 8414/9728 metadata — declare authorize_url and "
        f"token_url on its OAuthConfig instead."
    )


def clear_discovery_cache():
    _metadata_cache.clear()


# ----------------------------------------------------------------------
# Flow
# ----------------------------------------------------------------------
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
    if config.client_id:
        params["client_id"] = config.client_id
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


async def _post_token(metadata: dict, config: OAuthConfig, form: dict) -> dict:
    if config.client_id:
        form["client_id"] = config.client_id
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
    payload = await _post_token(metadata, config, {
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": redirect_uri(provider),
        "code_verifier": verifier,
    })

    db.save_provider_link(
        phone=row["phone"],
        provider=provider,
        access_token=encrypt(payload["access_token"]),
        refresh_token=encrypt(payload.get("refresh_token")),
        expires_at=_expiry_from(payload),
        scope=payload.get("scope"),
        client_id=config.client_id,
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
        payload = await _post_token(metadata, config, {
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
        })
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
