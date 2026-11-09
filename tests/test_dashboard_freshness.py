"""Dashboard read-side freshness surfacing (FEAT-003).

Verifies the service derives presentation-ready freshness fields (how long ago
a job was first seen, how long ago its source was last checked) and exposes the
SourceHealth timestamps on the company view — all logic in the service, not the
page.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy.orm import Session

from jobtracker.db.models import Company, Job, SourceHealth
from jobtracker.extract.work_auth import ELIGIBLE
from jobtracker.services import dashboard_service
from jobtracker.services.dashboard_service import humanize_ago


def _seed(session: Session) -> tuple[datetime, datetime]:
    now = datetime.now(UTC)
    first_seen = now - timedelta(hours=2)
    last_checked = now - timedelta(minutes=10)
    session.add(Company(id="acme", name="Acme", ats="greenhouse"))
    session.add(
        SourceHealth(
            company_id="acme",
            last_checked_at=last_checked,
            last_successful_check_at=last_checked,
            last_change_detected_at=first_seen,
            current_job_count=1,
            new_jobs_last_run=1,
            removed_jobs_last_run=0,
            error_count=0,
            last_status="ok",
        )
    )
    session.add(
        Job(
            job_id="job-1",
            company_id="acme",
            source_job_id="1",
            title="Mechanical Engineering Intern",
            classification="strong_match",
            fit_score=90,
            status="active",
            eligibility_status=ELIGIBLE,
            first_seen_at=first_seen,
            posted_at=first_seen,
        )
    )
    session.flush()
    return first_seen, last_checked


def test_humanize_ago_labels() -> None:
    now = datetime(2024, 1, 2, 12, 0, tzinfo=UTC)
    assert humanize_ago(None, now=now) is None
    assert humanize_ago(now - timedelta(seconds=10), now=now) == "just now"
    assert humanize_ago(now - timedelta(minutes=5), now=now) == "5m ago"
    assert humanize_ago(now - timedelta(hours=3), now=now) == "3h ago"
    assert humanize_ago(now - timedelta(days=2), now=now) == "2d ago"
    # Naive timestamps are treated as UTC.
    assert humanize_ago(datetime(2024, 1, 2, 11, 0), now=now) == "1h ago"
    # Future timestamps (clock skew) render as "just now".
    assert humanize_ago(now + timedelta(hours=1), now=now) == "just now"


def test_job_view_exposes_freshness_fields(session: Session) -> None:
    first_seen, last_checked = _seed(session)
    views = dashboard_service.list_jobs(session)
    assert len(views) == 1
    view = views[0]
    assert view.first_seen_at is not None
    assert view.first_seen_ago == "2h ago"
    # Source last-checked comes from SourceHealth, surfaced per job. SQLite
    # round-trips naive datetimes, so compare on the naive value.
    assert view.source_last_checked_at is not None
    assert view.source_last_checked_at.replace(tzinfo=None) == last_checked.replace(
        tzinfo=None
    )
    assert view.source_last_checked_ago == "10m ago"


def test_company_view_exposes_source_health_timestamps(session: Session) -> None:
    first_seen, last_checked = _seed(session)
    views = dashboard_service.list_company_views(session)
    assert len(views) == 1
    cv = views[0]
    assert cv.last_checked_at is not None
    assert cv.last_checked_at.replace(tzinfo=None) == last_checked.replace(tzinfo=None)
    assert cv.last_successful_check_at is not None
    assert cv.last_successful_check_at.replace(tzinfo=None) == last_checked.replace(
        tzinfo=None
    )
    assert cv.last_change_detected_at is not None
    assert cv.last_change_detected_at.replace(tzinfo=None) == first_seen.replace(
        tzinfo=None
    )
    assert cv.last_checked_ago == "10m ago"
    assert cv.new_jobs_last_run == 1
    assert cv.removed_jobs_last_run == 0
    assert cv.current_job_count == 1
    assert cv.last_error is None
    assert cv.error_count == 0
