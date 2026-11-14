"""New / changed / removed / active / expired detection (design 7).

Pure-ish detection layer keyed on ``(company_id, source_job_id)``. Given the
jobs fetched in the current run (as :class:`CanonicalJob` instances) and the
jobs already stored for the same company, :func:`detect_changes` upserts the
``jobs`` rows, appends :class:`JobEvent` audit rows, and refreshes the
``source_health`` counters for the run.

Rules (SPEC "DATA FRESHNESS REQUIREMENTS"):

* ``new`` -- source_job_id never seen before. In baseline mode the row is
  inserted as already-seen with no event and no notification flag.
* ``changed`` -- description hash changed; fields updated, ``changed`` event.
* ``active`` -- hash unchanged; ``last_seen_at`` bumped.
* ``removed`` -- previously active but absent from the current fetch;
  ``removed_at`` set, ``status=removed``, ``removed`` event.
* ``reactivated`` -- a previously removed row reappears in the fetch.
* ``expired`` -- a parsed application deadline is in the past.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime

from sqlalchemy.orm import Session

from jobtracker.db.models import Job, JobEvent, SourceHealth
from jobtracker.schemas.job import CanonicalJob, JobStatus

# Columns copied from a CanonicalJob onto a Job row (excludes identity and
# lifecycle fields managed by the detector itself).
_FIELD_COLUMNS: tuple[str, ...] = (
    "company_name",
    "title",
    "normalized_title",
    "location",
    "city",
    "province_state",
    "country",
    "remote_status",
    "employment_type",
    "department",
    "team",
    "description",
    "requirements",
    "preferred_qualifications",
    "skills",
    "description_hash",
    "application_url",
    "source_url",
    "source_type",
    "posted_at",
    "updated_at",
    "term_season",
    "term_start_date",
    "term_end_date",
    "duration_months",
    "duration_label",
    "citizenship_requirement",
    "work_authorization_requirement",
    "visa_sponsorship",
    "sponsorship_confidence",
    "sponsorship_evidence",
    "classification",
    "fit_score",
    "classification_confidence",
    "classification_reasons",
    "ai_analysis",
    "raw_payload",
    "eligibility_status",
)


@dataclass
class DetectionResult:
    """Summary of a single company's detection pass."""

    company_id: str
    new: list[str] = field(default_factory=list)
    changed: list[str] = field(default_factory=list)
    active: list[str] = field(default_factory=list)
    removed: list[str] = field(default_factory=list)
    reactivated: list[str] = field(default_factory=list)
    expired: list[str] = field(default_factory=list)
    current_job_count: int = 0


#: Metadata refreshed on every poll even when the description is unchanged.
_REFRESH_COLUMNS: tuple[str, ...] = (
    "title",
    "location",
    "application_url",
    "source_url",
    "posted_at",
)


def _now() -> datetime:
    return datetime.now(UTC)


def _copy_fields(job: Job, canonical: CanonicalJob) -> None:
    """Copy canonical content fields onto an ORM row."""
    for column in _FIELD_COLUMNS:
        setattr(job, column, getattr(canonical, column))


def _is_expired(job: Job, now: datetime) -> bool:
    """A parsed application deadline (term_end_date) in the past expires a job."""
    deadline = job.term_end_date
    if deadline is None:
        return False
    if deadline.tzinfo is None:
        deadline = deadline.replace(tzinfo=UTC)
    return deadline < now


def detect_changes(
    session: Session,
    company_id: str,
    fetched: list[CanonicalJob],
    *,
    baseline: bool = False,
) -> DetectionResult:
    """Reconcile fetched jobs against the stored rows for ``company_id``.

    Returns a :class:`DetectionResult` and refreshes the company's
    ``source_health`` run counters. In ``baseline`` mode newly inserted rows
    are seen silently (no ``new`` event) so an initial import does not mark
    every existing posting as false-new.
    """
    now = _now()
    result = DetectionResult(company_id=company_id)

    stored = {
        job.source_job_id: job
        for job in session.query(Job).filter(Job.company_id == company_id).all()
    }
    fetched_ids: set[str] = set()

    for canonical in fetched:
        source_job_id = canonical.source_job_id
        fetched_ids.add(source_job_id)
        existing = stored.get(source_job_id)

        if existing is None:
            job = Job(
                job_id=str(uuid.uuid4()),
                company_id=company_id,
                source_job_id=source_job_id,
                first_seen_at=now,
                last_seen_at=now,
                status=JobStatus.ACTIVE.value,
            )
            _copy_fields(job, canonical)
            session.add(job)
            if not baseline:
                _emit_event(session, job, "new", None, job.description_hash, now)
                result.new.append(job.job_id)
            continue

        was_removed = existing.status == JobStatus.REMOVED.value
        old_hash = existing.description_hash
        existing.last_seen_at = now
        existing.removed_at = None
        existing.status = JobStatus.ACTIVE.value

        if was_removed:
            _copy_fields(existing, canonical)
            _emit_event(session, existing, "reactivated", old_hash, existing.description_hash, now)
            result.reactivated.append(existing.job_id)
        elif old_hash != canonical.description_hash:
            _copy_fields(existing, canonical)
            _emit_event(session, existing, "changed", old_hash, existing.description_hash, now)
            result.changed.append(existing.job_id)
        else:
            # Same description, but refresh light metadata the connector may
            # now report better (e.g. Workday's country appended to the city,
            # a corrected apply link). Without this, connector fixes never
            # reach rows whose description is unchanged. Not a "change" event.
            for column in _REFRESH_COLUMNS:
                value = getattr(canonical, column, None)
                if value is not None and getattr(existing, column) != value:
                    setattr(existing, column, value)
            result.active.append(existing.job_id)

    # Rows previously active but absent from this fetch are now removed.
    for source_job_id, job in stored.items():
        if source_job_id in fetched_ids:
            continue
        if job.status == JobStatus.REMOVED.value:
            continue
        job.status = JobStatus.REMOVED.value
        job.removed_at = now
        _emit_event(session, job, "removed", job.description_hash, None, now)
        result.removed.append(job.job_id)

    # Expiry pass over all of the company's active rows.
    for job in session.query(Job).filter(Job.company_id == company_id).all():
        if job.status == JobStatus.ACTIVE.value and _is_expired(job, now):
            job.status = JobStatus.EXPIRED.value
            result.expired.append(job.job_id)

    result.current_job_count = len(fetched_ids)
    _update_source_health(session, result, now)
    return result


def _emit_event(
    session: Session,
    job: Job,
    event_type: str,
    old_hash: str | None,
    new_hash: str | None,
    now: datetime,
) -> None:
    session.add(
        JobEvent(
            job_id=job.job_id,
            company_id=job.company_id,
            event_type=event_type,
            old_hash=old_hash,
            new_hash=new_hash,
            created_at=now,
        )
    )


def _update_source_health(
    session: Session, result: DetectionResult, now: datetime
) -> None:
    health = session.get(SourceHealth, result.company_id)
    if health is None:
        health = SourceHealth(company_id=result.company_id)
        session.add(health)

    health.last_checked_at = now
    health.last_successful_check_at = now
    health.current_job_count = result.current_job_count
    health.new_jobs_last_run = len(result.new)
    health.removed_jobs_last_run = len(result.removed)
    if result.new or result.changed or result.removed or result.reactivated:
        health.last_change_detected_at = now
    health.last_status = "ok"
