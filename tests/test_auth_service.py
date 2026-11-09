"""Tests for the Phase-2 authentication service."""

from __future__ import annotations

import pytest
from sqlalchemy.orm import Session

from jobtracker.db.models import User, UserProfile
from jobtracker.services import auth_service
from jobtracker.services.auth_service import AuthError


def test_register_and_authenticate(session: Session) -> None:
    user = auth_service.register(session, "Alice@Example.com", "s3cret-pass")
    assert user.id is not None
    # Email is normalized (trimmed + lowercased).
    assert user.email == "alice@example.com"

    found = auth_service.authenticate(session, "alice@example.com", "s3cret-pass")
    assert found is not None
    assert found.id == user.id

    # Case-insensitive email login works too.
    assert auth_service.authenticate(session, "ALICE@EXAMPLE.COM", "s3cret-pass")


def test_wrong_password_rejected(session: Session) -> None:
    auth_service.register(session, "bob@example.com", "correct-horse")
    assert auth_service.authenticate(session, "bob@example.com", "wrong") is None
    assert auth_service.authenticate(session, "nobody@example.com", "x") is None


def test_password_never_stored_in_plaintext(session: Session) -> None:
    secret = "my-plaintext-password"
    auth_service.register(session, "carol@example.com", secret)
    row = session.query(User).filter(User.email == "carol@example.com").one()
    assert row.password_hash is not None
    assert row.password_hash != secret
    assert secret not in (row.password_hash or "")
    # bcrypt hashes start with the $2 identifier.
    assert row.password_hash.startswith("$2")


def test_first_user_is_admin_rest_are_members(session: Session) -> None:
    first = auth_service.register(session, "first@example.com", "pw-one")
    second = auth_service.register(session, "second@example.com", "pw-two")
    assert first.role == auth_service.ROLE_ADMIN
    assert second.role == auth_service.ROLE_MEMBER


def test_duplicate_email_rejected(session: Session) -> None:
    auth_service.register(session, "dup@example.com", "pw")
    with pytest.raises(AuthError):
        auth_service.register(session, "DUP@example.com", "pw2")


def test_register_seeds_profile(session: Session) -> None:
    seed = {"program": "Mechanical Engineering", "profile_version": 1}
    user = auth_service.register(
        session, "seed@example.com", "pw", seed_profile=seed
    )
    profile = (
        session.query(UserProfile).filter(UserProfile.user_id == user.id).one()
    )
    assert profile.profile_version == 1
    assert profile.data["program"] == "Mechanical Engineering"


def test_empty_credentials_rejected(session: Session) -> None:
    with pytest.raises(AuthError):
        auth_service.register(session, "", "pw")
    with pytest.raises(AuthError):
        auth_service.register(session, "x@example.com", "")
