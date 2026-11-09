"""Tests for the Phase-2 saved-job and application-pipeline service."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.orm import Session

from jobtracker.db.models import Company, Job
from jobtracker.extract.work_auth import ELIGIBLE
from jobtracker.services import auth_service, dashboard_service, saved_job_service
from jobtracker.services.saved_job_service import SavedJobError


def _seed_job(session: Session, job_id: str = "job-1") -> None:
    if session.get(Company, "acme") is None:
        session.add(Company(id="acme", name="Acme", ats="greenhouse"))
    session.add(
        Job(
            job_id=job_id,
            company_id="acme",
            source_job_id=job_id,
            title="Mechanical Engineering Intern",
            classification="strong_match",
            fit_score=90,
            status="active",
            eligibility_status=ELIGIBLE,
            first_seen_at=datetime.now(UTC) - timedelta(hours=1),
        )
    )
    session.flush()


def _user(session: Session, email: str = "u@example.com") -> int:
    return auth_service.register(session, email, "pw").id


def test_save_and_unsave(session: Session) -> None:
    _seed_job(session)
    uid = _user(session)
    assert not saved_job_service.is_saved(session, uid, "job-1")

    saved_job_service.save_job(session, uid, "job-1")
    assert saved_job_service.is_saved(session, uid, "job-1")
    # Idempotent: saving again does not create a duplicate.
    saved_job_service.save_job(session, uid, "job-1")
    assert len(saved_job_service.list_saved_jobs(session, uid)) == 1

    assert saved_job_service.unsave_job(session, uid, "job-1") is True
    assert not saved_job_service.is_saved(session, uid, "job-1")
    assert saved_job_service.unsave_job(session, uid, "job-1") is False


def test_toggle_saved(session: Session) -> None:
    _seed_job(session)
    uid = _user(session)
    assert saved_job_service.toggle_saved(session, uid, "job-1") is True
    assert saved_job_service.toggle_saved(session, uid, "job-1") is False


def test_pipeline_status_transitions(session: Session) -> None:
    _seed_job(session)
    uid = _user(session)
    row = saved_job_service.set_status(session, uid, "job-1", "applying")
    assert row.status == "applying"
    # Setting a status auto-saves the job.
    assert saved_job_service.is_saved(session, uid, "job-1")

    deadline = datetime.now(UTC) + timedelta(days=7)
    row = saved_job_service.set_status(
        session, uid, "job-1", "submitted", note="sent", deadline=deadline
    )
    assert row.status == "submitted"
    assert row.note == "sent"
    assert row.deadline is not None

    assert saved_job_service.status_map(session, uid) == {"job-1": "submitted"}


def test_invalid_status_rejected(session: Session) -> None:
    _seed_job(session)
    uid = _user(session)
    with pytest.raises(SavedJobError):
        saved_job_service.set_status(session, uid, "job-1", "not-a-status")


def test_saved_jobs_are_user_scoped(session: Session) -> None:
    _seed_job(session)
    u1 = _user(session, "a@example.com")
    u2 = _user(session, "b@example.com")
    saved_job_service.save_job(session, u1, "job-1")
    assert saved_job_service.is_saved(session, u1, "job-1")
    assert not saved_job_service.is_saved(session, u2, "job-1")


def test_dashboard_user_scoping_enriches_views(session: Session) -> None:
    _seed_job(session)
    uid = _user(session)
    saved_job_service.set_status(session, uid, "job-1", "applying")

    # Without a user_id the views are unchanged (Phase-1 behaviour).
    anon = dashboard_service.list_jobs(session)[0]
    assert anon.is_saved is False
    assert anon.application_status is None

    # With a user_id the views carry per-user state.
    scoped = dashboard_service.list_jobs(session, user_id=uid)[0]
    assert scoped.is_saved is True
    assert scoped.application_status == "applying"

    # The saved-jobs view returns only hearted jobs.
    saved_views = dashboard_service.list_saved_job_views(session, uid)
    assert [v.job_id for v in saved_views] == ["job-1"]
