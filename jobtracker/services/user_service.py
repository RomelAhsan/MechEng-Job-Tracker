"""Per-user personalization service (Phase 2, design 3.6).

Owns the per-user profile (with ``profile_version`` bumping on every edit, per
the SPEC AI cache-invalidation rule), notification preferences, and CRUD for
named saved filters / subscriptions (the FEAT-005 foundation).

All business logic lives here so the Streamlit pages stay thin views.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from jobtracker.db.models import SavedFilter, UserProfile

logger = logging.getLogger("jobtracker.services.user")


def _now() -> datetime:
    return datetime.now(UTC)


class UserServiceError(ValueError):
    """Raised on invalid personalization operations."""


@dataclass
class SavedFilterView:
    """Presentation-ready view of a saved filter / subscription."""

    id: int
    name: str
    filter_json: dict
    notify: bool
    channel: str | None
    created_at: datetime | None


# --- Profile ---------------------------------------------------------------


def get_profile(session: Session, user_id: int) -> UserProfile | None:
    """Return the user's profile row or None."""
    return session.scalars(
        select(UserProfile).where(UserProfile.user_id == user_id)
    ).first()


def _ensure_profile(session: Session, user_id: int) -> UserProfile:
    profile = get_profile(session, user_id)
    if profile is None:
        profile = UserProfile(
            user_id=user_id,
            profile_version=1,
            data={},
            notification_prefs={},
            updated_at=_now(),
        )
        session.add(profile)
        session.flush()
    return profile


def update_profile(session: Session, user_id: int, data: dict) -> UserProfile:
    """Replace the profile ``data`` and bump ``profile_version`` on change.

    The version is only incremented when the stored data actually changes, so
    no-op saves do not needlessly invalidate Phase 3 AI caches.
    """
    profile = _ensure_profile(session, user_id)
    new_data = dict(data)
    # Keep the version field inside the JSON in sync with the column; callers
    # should not hand-edit it.
    current = dict(profile.data or {})
    current.pop("profile_version", None)
    candidate = dict(new_data)
    candidate.pop("profile_version", None)

    if candidate != current:
        profile.profile_version = (profile.profile_version or 1) + 1
        logger.info(
            "Profile for user %s edited; bumped to version %s",
            user_id,
            profile.profile_version,
        )
    candidate["profile_version"] = profile.profile_version
    profile.data = candidate
    profile.updated_at = _now()
    session.flush()
    return profile


# --- Notification preferences ---------------------------------------------


def get_notification_prefs(session: Session, user_id: int) -> dict:
    """Return the user's notification preferences (empty dict if unset)."""
    profile = get_profile(session, user_id)
    if profile is None:
        return {}
    return dict(profile.notification_prefs or {})


def set_notification_prefs(session: Session, user_id: int, prefs: dict) -> dict:
    """Replace the user's notification preferences and return them."""
    profile = _ensure_profile(session, user_id)
    profile.notification_prefs = dict(prefs)
    profile.updated_at = _now()
    session.flush()
    return dict(profile.notification_prefs)


# --- Saved filters / subscriptions ----------------------------------------


def _to_filter_view(row: SavedFilter) -> SavedFilterView:
    return SavedFilterView(
        id=row.id,
        name=row.name or "",
        filter_json=dict(row.filter_json or {}),
        notify=bool(row.notify),
        channel=row.channel,
        created_at=row.created_at,
    )


def create_saved_filter(
    session: Session,
    user_id: int,
    name: str,
    filter_json: dict,
    *,
    notify: bool = False,
    channel: str | None = None,
) -> SavedFilterView:
    """Create a named saved filter / subscription for a user."""
    if not name or not name.strip():
        raise UserServiceError("Saved filter requires a name.")
    row = SavedFilter(
        user_id=user_id,
        name=name.strip(),
        filter_json=dict(filter_json or {}),
        notify=notify,
        channel=channel,
        created_at=_now(),
    )
    session.add(row)
    session.flush()
    return _to_filter_view(row)


def list_saved_filters(session: Session, user_id: int) -> list[SavedFilterView]:
    """List a user's saved filters, newest first."""
    rows = session.scalars(
        select(SavedFilter)
        .where(SavedFilter.user_id == user_id)
        .order_by(SavedFilter.id.desc())
    ).all()
    return [_to_filter_view(r) for r in rows]


def update_saved_filter(
    session: Session,
    user_id: int,
    filter_id: int,
    *,
    name: str | None = None,
    filter_json: dict | None = None,
    notify: bool | None = None,
    channel: str | None = None,
) -> SavedFilterView:
    """Update fields on a user's saved filter."""
    row = _require_filter(session, user_id, filter_id)
    if name is not None:
        if not name.strip():
            raise UserServiceError("Saved filter requires a name.")
        row.name = name.strip()
    if filter_json is not None:
        row.filter_json = dict(filter_json)
    if notify is not None:
        row.notify = notify
    if channel is not None:
        row.channel = channel
    session.flush()
    return _to_filter_view(row)


def delete_saved_filter(session: Session, user_id: int, filter_id: int) -> None:
    """Delete a user's saved filter."""
    row = _require_filter(session, user_id, filter_id)
    session.delete(row)
    session.flush()


def _require_filter(session: Session, user_id: int, filter_id: int) -> SavedFilter:
    row = session.get(SavedFilter, filter_id)
    if row is None or row.user_id != user_id:
        raise UserServiceError(f"Unknown saved filter id '{filter_id}' for user.")
    return row
