"""Saved-job and application-pipeline service (Phase 2, design 3.6).

Owns the per-user "hearted" jobs and the application pipeline (status, note,
deadline). All business logic lives here; the Streamlit pages are thin callers.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from jobtracker.db.models import ApplicationStatus, SavedJob

logger = logging.getLogger("jobtracker.services.saved_job")

#: Allowed application-pipeline statuses, in SPEC order.
PIPELINE_STATUSES: tuple[str, ...] = (
    "saved",
    "researching",
    "applying",
    "submitted",
    "interviewing",
    "offer",
    "rejected",
    "closed",
)


def _now() -> datetime:
    return datetime.now(UTC)


class SavedJobError(ValueError):
    """Raised on invalid saved-job / pipeline operations."""


# --- Saved (hearted) jobs --------------------------------------------------


def is_saved(session: Session, user_id: int, job_id: str) -> bool:
    """Return True if the user has saved this job."""
    return _get_saved(session, user_id, job_id) is not None


def _get_saved(session: Session, user_id: int, job_id: str) -> SavedJob | None:
    return session.scalars(
        select(SavedJob).where(
            SavedJob.user_id == user_id, SavedJob.job_id == job_id
        )
    ).first()


def save_job(
    session: Session, user_id: int, job_id: str, *, note: str | None = None
) -> SavedJob:
    """Save/heart a job for a user (idempotent)."""
    existing = _get_saved(session, user_id, job_id)
    if existing is not None:
        if note is not None:
            existing.note = note
            session.flush()
        return existing
    row = SavedJob(user_id=user_id, job_id=job_id, note=note, created_at=_now())
    session.add(row)
    session.flush()
    return row


def unsave_job(session: Session, user_id: int, job_id: str) -> bool:
    """Remove a saved job; returns True if a row was removed."""
    existing = _get_saved(session, user_id, job_id)
    if existing is None:
        return False
    session.delete(existing)
    session.flush()
    return True


def toggle_saved(session: Session, user_id: int, job_id: str) -> bool:
    """Toggle saved state; returns the new saved state (True = now saved)."""
    if _get_saved(session, user_id, job_id) is not None:
        unsave_job(session, user_id, job_id)
        return False
    save_job(session, user_id, job_id)
    return True


def list_saved_job_ids(session: Session, user_id: int) -> set[str]:
    """Return the set of job ids saved by a user."""
    return {
        job_id
        for (job_id,) in session.execute(
            select(SavedJob.job_id).where(SavedJob.user_id == user_id)
        )
        if job_id is not None
    }


def list_saved_jobs(session: Session, user_id: int) -> list[SavedJob]:
    """Return a user's saved-job rows, newest first."""
    return list(
        session.scalars(
            select(SavedJob)
            .where(SavedJob.user_id == user_id)
            .order_by(SavedJob.id.desc())
        ).all()
    )


# --- Application pipeline --------------------------------------------------


def get_status(session: Session, user_id: int, job_id: str) -> ApplicationStatus | None:
    """Return a user's application status for a job or None."""
    return session.scalars(
        select(ApplicationStatus).where(
            ApplicationStatus.user_id == user_id,
            ApplicationStatus.job_id == job_id,
        )
    ).first()


def set_status(
    session: Session,
    user_id: int,
    job_id: str,
    status: str,
    *,
    note: str | None = None,
    deadline: datetime | None = None,
) -> ApplicationStatus:
    """Set (upsert) a user's application status for a job.

    Setting a pipeline status also auto-saves the job so the pipeline and the
    hearted list stay consistent.
    """
    if status not in PIPELINE_STATUSES:
        raise SavedJobError(
            f"Invalid status '{status}'. Expected one of {PIPELINE_STATUSES}."
        )
    row = get_status(session, user_id, job_id)
    if row is None:
        row = ApplicationStatus(user_id=user_id, job_id=job_id)
        session.add(row)
    row.status = status
    if note is not None:
        row.note = note
    if deadline is not None:
        row.deadline = deadline
    row.updated_at = _now()
    # Keep the job in the saved list so it appears on the pipeline page.
    save_job(session, user_id, job_id)
    session.flush()
    return row


def status_map(session: Session, user_id: int) -> dict[str, str]:
    """Return {job_id: status} for a user's pipeline entries."""
    return {
        job_id: status
        for job_id, status in session.execute(
            select(ApplicationStatus.job_id, ApplicationStatus.status).where(
                ApplicationStatus.user_id == user_id
            )
        )
        if job_id is not None and status is not None
    }
