"""Password hashing primitives (Phase 2 auth).

Centralizes bcrypt hashing/verification so no other module touches the crypto
library directly and plaintext passwords never leave this boundary. bcrypt is
used directly (the ``passlib[bcrypt]`` dependency ships it) because passlib's
legacy backend probe is incompatible with bcrypt 5.x.

bcrypt truncates inputs beyond 72 bytes, so passwords are encoded and bounded
here consistently for both hashing and verification.
"""

from __future__ import annotations

import bcrypt

_MAX_BCRYPT_BYTES = 72


def _encode(password: str) -> bytes:
    return password.encode("utf-8")[:_MAX_BCRYPT_BYTES]


def hash_password(password: str) -> str:
    """Return a bcrypt hash for ``password`` (never stores plaintext)."""
    if not password:
        raise ValueError("Password must not be empty.")
    return bcrypt.hashpw(_encode(password), bcrypt.gensalt()).decode("ascii")


def verify_password(password: str, password_hash: str | None) -> bool:
    """Return True if ``password`` matches ``password_hash``."""
    if not password or not password_hash:
        return False
    try:
        return bcrypt.checkpw(_encode(password), password_hash.encode("ascii"))
    except (ValueError, TypeError):
        return False
