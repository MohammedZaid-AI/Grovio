"""
Credential vault.

The ONLY component that ever holds a decrypted provider token. Skills, the
planner and the conversation layer receive link *status*, never credentials.

Refresh is lazy — on use, never on a timer. A background refresher would keep
tokens alive for users who aren't talking to us, which is both wasteful and a
larger window of live credentials than necessary.
"""
from datetime import datetime, timedelta, timezone

import db
from core.crypto import decrypt
from core.logger import logger

from ai.providers import oauth

# Refresh this far before actual expiry so a token cannot die mid-request.
EXPIRY_SKEW_SECONDS = 60

LINKED = "LINKED"
REVOKED = "REVOKED"


class NeedsLink(Exception):
    """This user must (re)connect the provider before it can be used.

    Carries no credential material — it is safe to let this reach the skills
    layer, which turns it into a link prompt.
    """

    def __init__(self, provider: str, reason: str = "not_linked"):
        super().__init__(f"{provider} requires linking ({reason})")
        self.provider = provider
        self.reason = reason


def is_linked(phone: str, provider: str) -> bool:
    link = db.get_provider_link(phone, provider)
    return bool(link and link["status"] == LINKED and link["access_token"])


def linked_providers(phone: str) -> list:
    return db.get_linked_providers(phone)


def _is_expiring(expires_at) -> bool:
    """True if the token is gone or about to be. Unparseable timestamps are
    treated as expiring: refreshing needlessly is cheap, using a dead token is
    a failed order."""
    if not expires_at:
        return False   # no expiry recorded == does not expire
    try:
        deadline = datetime.strptime(str(expires_at)[:19], "%Y-%m-%d %H:%M:%S")
    except ValueError:
        return True
    return deadline <= datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(
        seconds=EXPIRY_SKEW_SECONDS
    )


async def access_token(phone: str, provider: str, config: oauth.OAuthConfig) -> str:
    """Return a usable access token, refreshing if needed.

    Raises NeedsLink when the user must re-authorise. Callers must let that
    propagate rather than retrying — a revoked grant does not heal.
    """
    link = db.get_provider_link(phone, provider)
    if not link or link["status"] != LINKED:
        raise NeedsLink(provider, "not_linked")

    if _is_expiring(link["expires_at"]):
        refresh_token = decrypt(link["refresh_token"])
        if not refresh_token:
            _revoke(phone, provider, "expired_no_refresh_token")
            raise NeedsLink(provider, "expired")

        if not await oauth.refresh(phone, provider, config, refresh_token):
            # The provider rejected the refresh — typically the user revoked
            # access on their side. Only a fresh authorisation fixes this.
            _revoke(phone, provider, "refresh_rejected")
            raise NeedsLink(provider, "revoked")

        link = db.get_provider_link(phone, provider)

    token = decrypt(link["access_token"])
    if not token:
        # Unreadable ciphertext (rotated or wrong key). Same recovery path.
        _revoke(phone, provider, "undecryptable")
        raise NeedsLink(provider, "unreadable")

    return token


def _revoke(phone: str, provider: str, reason: str) -> None:
    logger.info(f"[vault] revoking {provider} link ({reason})")
    db.revoke_provider_link(phone, provider)


def unlink(phone: str, provider: str) -> None:
    """Explicit user-initiated disconnect: forget the credentials entirely."""
    db.revoke_provider_link(phone, provider, delete=True)
    logger.info(f"[vault] {provider} unlinked by user request")
