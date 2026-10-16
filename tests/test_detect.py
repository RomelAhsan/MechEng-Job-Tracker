"""Tests for new/changed/removed/reactivated detection and baseline mode."""

from __future__ import annotations

from sqlalchemy.orm import Session

from jobtracker.db.models import Company, JobEvent, SourceHealth
from jobtracker.detect.diff import detect_changes
from jobtracker.schemas.job import CanonicalJob


def _company(session: Session, company_id: str = "acme") -> None:
    session.add(Company(id=company_id, name="Acme", ats="greenhouse"))
    session.flush()


def _canonical(source_job_id: str, title: str, description_hash: str) -> CanonicalJob:
    return CanonicalJob(
        source_job_id=source_job_id,
        company_id="acme",
        company_name="Acme",
        title=title,
        description=title,
        description_hash=description_hash,
        application_url="https://apply.example/1",
        source_url="https://jobs.example/1",
    )


def test_new_job_detected_and_event_emitted(session: Session) -> None:
    _company(session)
    result = detect_changes(session, "acme", [_canonical("1", "A", "h1")])
    session.flush()

    assert len(result.new) == 1
    events = session.query(JobEvent).all()
    assert len(events) == 1
    assert events[0].event_type == "new"


def test_changed_job_emits_changed_event(session: Session) -> None:
    _company(session)
    detect_changes(session, "acme", [_canonical("1", "A", "h1")])
    session.flush()

    result = detect_changes(session, "acme", [_canonical("1", "A changed", "h2")])
    session.flush()

    assert len(result.changed) == 1
    assert not result.new
    event_types = {e.event_type for e in session.query(JobEvent).all()}
    assert "changed" in event_types


def test_active_job_bumps_without_event(session: Session) -> None:
    _company(session)
    detect_changes(session, "acme", [_canonical("1", "A", "h1")])
    session.flush()

    result = detect_changes(session, "acme", [_canonical("1", "A", "h1")])
    session.flush()

    assert len(result.active) == 1
    assert not result.changed
    assert session.query(JobEvent).count() == 1  # only the original 'new'


def test_removed_job_marked_and_event(session: Session) -> None:
    _company(session)
    detect_changes(session, "acme", [_canonical("1", "A", "h1")])
    session.flush()

    result = detect_changes(session, "acme", [])
    session.flush()

    assert len(result.removed) == 1
    event_types = {e.event_type for e in session.query(JobEvent).all()}
    assert "removed" in event_types
    health = session.get(SourceHealth, "acme")
    assert health.removed_jobs_last_run == 1


def test_reactivated_job(session: Session) -> None:
    _company(session)
    detect_changes(session, "acme", [_canonical("1", "A", "h1")])
    detect_changes(session, "acme", [])  # removed
    session.flush()

    result = detect_changes(session, "acme", [_canonical("1", "A", "h1")])
    session.flush()

    assert len(result.reactivated) == 1
    event_types = [e.event_type for e in session.query(JobEvent).all()]
    assert "reactivated" in event_types


def test_baseline_mode_suppresses_false_new(session: Session) -> None:
    _company(session)
    result = detect_changes(
        session, "acme", [_canonical("1", "A", "h1"), _canonical("2", "B", "h2")],
        baseline=True,
    )
    session.flush()

    assert not result.new
    assert session.query(JobEvent).count() == 0
    health = session.get(SourceHealth, "acme")
    assert health.current_job_count == 2
    assert health.new_jobs_last_run == 0
