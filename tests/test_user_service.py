"""Tests for the Phase-2 per-user personalization service."""

from __future__ import annotations

import pytest
from sqlalchemy.orm import Session

from jobtracker.services import auth_service, user_service
from jobtracker.services.user_service import UserServiceError


def _make_user(session: Session, email: str = "u@example.com") -> int:
    return auth_service.register(session, email, "pw").id


def test_profile_update_bumps_version_on_change(session: Session) -> None:
    user_id = _make_user(session)
    profile = user_service.get_profile(session, user_id)
    assert profile.profile_version == 1

    updated = user_service.update_profile(session, user_id, {"year": 3})
    assert updated.profile_version == 2
    assert updated.data["year"] == 3
    assert updated.data["profile_version"] == 2


def test_profile_update_noop_does_not_bump_version(session: Session) -> None:
    user_id = _make_user(session)
    user_service.update_profile(session, user_id, {"year": 3})
    before = user_service.get_profile(session, user_id).profile_version
    # Saving identical data (ignoring profile_version) must not bump.
    after = user_service.update_profile(
        session, user_id, {"year": 3, "profile_version": 999}
    ).profile_version
    assert after == before


def test_notification_prefs_roundtrip(session: Session) -> None:
    user_id = _make_user(session)
    assert user_service.get_notification_prefs(session, user_id) == {}
    prefs = {"email_enabled": True, "email": "u@example.com", "channel": "email"}
    user_service.set_notification_prefs(session, user_id, prefs)
    assert user_service.get_notification_prefs(session, user_id) == prefs


def test_saved_filter_crud(session: Session) -> None:
    user_id = _make_user(session)
    created = user_service.create_saved_filter(
        session,
        user_id,
        "Ontario Mech",
        {"countries": ["Canada"]},
        notify=True,
        channel="email",
    )
    assert created.id is not None
    assert created.notify is True

    listed = user_service.list_saved_filters(session, user_id)
    assert len(listed) == 1
    assert listed[0].name == "Ontario Mech"

    user_service.update_saved_filter(
        session, user_id, created.id, name="Renamed", notify=False
    )
    refreshed = user_service.list_saved_filters(session, user_id)[0]
    assert refreshed.name == "Renamed"
    assert refreshed.notify is False

    user_service.delete_saved_filter(session, user_id, created.id)
    assert user_service.list_saved_filters(session, user_id) == []


def test_saved_filter_requires_name(session: Session) -> None:
    user_id = _make_user(session)
    with pytest.raises(UserServiceError):
        user_service.create_saved_filter(session, user_id, "  ", {})


def test_saved_filters_are_user_scoped(session: Session) -> None:
    u1 = _make_user(session, "a@example.com")
    u2 = _make_user(session, "b@example.com")
    f = user_service.create_saved_filter(session, u1, "mine", {})
    # Another user cannot update/delete someone else's filter.
    with pytest.raises(UserServiceError):
        user_service.update_saved_filter(session, u2, f.id, name="x")
    with pytest.raises(UserServiceError):
        user_service.delete_saved_filter(session, u2, f.id)
