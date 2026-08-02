"""
Encryption at rest for provider credentials.

Fernet (AES-128-CBC + HMAC-SHA256) from `cryptography`, which is already a
dependency. We do not roll our own crypto.

FAILS CLOSED: with no key configured, encrypt() raises. There is deliberately no
plaintext fallback — a fallback is exactly how unencrypted OAuth tokens end up
in a production database.

Generate a key:
    python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"

and set it as TOKEN_ENCRYPTION_KEY. Keep it OUT of the database it protects —
a key stored alongside the ciphertext protects nothing.
"""
import os

from cryptography.fernet import Fernet, InvalidToken

ENV_VAR = "TOKEN_ENCRYPTION_KEY"

_cipher = None
_cipher_key = None


class CryptoNotConfigured(RuntimeError):
    """Raised when encryption is required but no key is configured."""


def _get_cipher() -> Fernet:
    """Build (and cache) the cipher. Re-reads if the env key changes, so tests
    can swap keys without reloading the module."""
    global _cipher, _cipher_key

    key = os.getenv(ENV_VAR, "").strip()
    if not key:
        raise CryptoNotConfigured(
            f"{ENV_VAR} is not set. Provider tokens cannot be stored without "
            f"encryption. Generate one with Fernet.generate_key()."
        )

    if _cipher is None or _cipher_key != key:
        try:
            _cipher = Fernet(key.encode())
        except (ValueError, TypeError) as e:
            raise CryptoNotConfigured(f"{ENV_VAR} is not a valid Fernet key: {e}") from e
        _cipher_key = key

    return _cipher


def is_configured() -> bool:
    """True if encryption is usable. Lets callers fail with a clear message
    instead of an exception mid-flow."""
    try:
        _get_cipher()
        return True
    except CryptoNotConfigured:
        return False


def encrypt(plaintext: str | None) -> str | None:
    """Encrypt a secret for storage. None passes through (absent, not empty)."""
    if plaintext is None:
        return None
    return _get_cipher().encrypt(plaintext.encode()).decode()


def decrypt(ciphertext: str | None) -> str | None:
    """Decrypt a stored secret.

    Returns None if the value cannot be decrypted (wrong or rotated key, or
    tampering) rather than raising: the caller treats an unreadable credential
    the same as a missing one and re-links, which is the recoverable path.
    """
    if ciphertext is None:
        return None
    try:
        return _get_cipher().decrypt(ciphertext.encode()).decode()
    except (InvalidToken, CryptoNotConfigured):
        return None
