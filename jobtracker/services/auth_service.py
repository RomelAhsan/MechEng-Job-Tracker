"""Authentication service (Phase 2, design 3.6).

Owns user registration and credential verification. Passwords are bcrypt-hashed
via :mod:`jobtracker.services.security`; plaintext is never stored or logged.
The first registered user becomes the ``admin`` (owner of the shared company
registry); everyone after is a ``member``.

All business logic lives here so the CLI and Streamlit layers are thin callers.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from jobtracker.db.models import User, UserProfile
from jobtracker.services.security import hash_password, verify_password

logger = logging.getLogger("jobtracker.services.auth")

ROLE_ADMIN = "admin"
ROLE_MEMBER = "member"


def _now() -> datetime:
    return datetime.now(UTC)


def _normalize_email(email: str) -> str:
    return email.strip().lower()


class AuthError(ValueError):
    """Raised when registration or authentication cannot proceed."""


def user_count(session: Session) -> int:
    """Return the number of registered users."""
    return int(session.scalar(select(func.count()).select_from(User)) or 0)


def get_by_email(session: Session, email: str) -> User | None:
    """Return the user with ``email`` (case-insensitive) or None."""
    normalized = _normalize_email(email)
    return session.scalars(
        select(User).where(func.lower(User.email) == normalized)
    ).first()


def register(
    session: Session,
    email: str,
    password: str,
    *,
    display_name: str | None = None,
    role: str | None = None,
    seed_profile: dict | None = None,
) -> User:
    """Register a new user with a bcrypt-hashed password.

    The first registered user is forced to ``admin``; subsequent users default
    to ``member`` unless an explicit ``role`` is given. A per-user
    :class:`UserProfile` row is created, seeded from ``seed_profile`` when given
    (the first user is seeded from ``config/user.yaml`` by the caller).
    """
    normalized = _normalize_email(email)
    if not normalized:
        raise AuthError("Email is required.")
    if not password:
        raise AuthError("Password is required.")
    if get_by_email(session, normalized) is not None:
        raise AuthError(f"A user with email '{normalized}' already exists.")

    is_first = user_count(session) == 0
    resolved_role = ROLE_ADMIN if is_first else (role or ROLE_MEMBER)

    user = User(
        email=normalized,
        display_name=display_name or normalized,
        password_hash=hash_password(password),
        role=resolved_role,
        created_at=_now(),
    )
    session.add(user)
    session.flush()

    profile = UserProfile(
        user_id=user.id,
        profile_version=1,
        data=dict(seed_profile or {}),
        notification_prefs={},
        updated_at=_now(),
    )
    session.add(profile)
    session.flush()

    logger.info("Registered user %s (role=%s)", normalized, resolved_role)
    return user


def authenticate(session: Session, email: str, password: str) -> User | None:
    """Return the matching user when credentials are valid, else None."""
    user = get_by_email(session, email)
    if user is None:
        return None
    if not verify_password(password, user.password_hash):
        return None
    return user


def get_user(session: Session, user_id: int) -> User | None:
    """Return the user by id or None."""
    return session.get(User, user_id)
