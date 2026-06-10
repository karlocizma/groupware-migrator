"""Credential vault: Fernet (AES-128-CBC + HMAC-SHA256) encryption for sensitive data.

Set VAULT_KEY to a 32-byte value encoded as URL-safe base64 (no padding).
Generate one with: python3 -c "import secrets,base64; print(base64.urlsafe_b64encode(secrets.token_bytes(32)).rstrip(b'=').decode())"
"""
from __future__ import annotations

import base64
import os

_VAULT_PREFIX = "vault:"


def _load_fernet():
    from cryptography.fernet import Fernet  # noqa: PLC0415

    key_b64 = os.environ.get("VAULT_KEY", "")
    if not key_b64:
        return None
    # Add padding and decode
    padded = key_b64 + "=" * (-len(key_b64) % 4)
    raw = base64.urlsafe_b64decode(padded)
    if len(raw) != 32:
        raise ValueError("VAULT_KEY must decode to exactly 32 bytes.")
    # Fernet key is base64url-encoded 32 bytes
    fernet_key = base64.urlsafe_b64encode(raw)
    return Fernet(fernet_key)


def encrypt(plaintext: str) -> str:
    """Encrypt plaintext if VAULT_KEY is set; return plaintext unchanged otherwise."""
    f = _load_fernet()
    if f is None:
        return plaintext
    return _VAULT_PREFIX + f.encrypt(plaintext.encode()).decode()


def decrypt(value: str) -> str:
    """Decrypt a vault-prefixed string; return unchanged if not encrypted."""
    if not value.startswith(_VAULT_PREFIX):
        return value
    f = _load_fernet()
    if f is None:
        raise ValueError("VAULT_KEY env var is required to decrypt vault-encrypted values.")
    return f.decrypt(value[len(_VAULT_PREFIX):].encode()).decode()


def is_encrypted(value: str) -> bool:
    return value.startswith(_VAULT_PREFIX)


def vault_enabled() -> bool:
    return bool(os.environ.get("VAULT_KEY", ""))
