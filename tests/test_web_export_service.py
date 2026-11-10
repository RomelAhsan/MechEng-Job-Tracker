"""Tests for the static web feed (always-on React site data source).

The feed must contain exactly the eligible "Relevant For Me" set -- i.e. it
must inherit the Stage-1 hard filter and the Stage-2 AI gate from
``dashboard_service.list_relevant_jobs`` rather than re-deriving eligibility --
and it must always be a well-formed file, even when empty.
"""

from __future__ import annotations

import json

from sqlalchemy.orm import Session

from jobtracker.db.models import Company, Job
from jobtracker.services import web_export_service


def _company(session: Session) -> None:
    session.add(Company(id="acme", name="Acme Robotics", ats="greenhouse", active=True))


def _job(session: Session, job_id: str, title: str, *, eligible: bool | None) -> None:
    session.add(
        Job(
            job_id=job_id,
            company_id="acme",
            source_job_id=job_id,
            title=title,
            location="Toronto, Ontario, Canada",
            term_season="spring",
            duration_months=4,
            duration_label="4-month",
            fit_score=90,
            application_url=f"https://apply.example/{job_id}",
            source_url=f"https://src.example/{job_id}",
            description="Design mechanical systems.",
            status="active",
            classification="strong_match",
            eligible=eligible,
        )
    )


def test_feed_has_wellformed_shape_when_empty(session: Session) -> None:
    feed = web_export_service.build_feed(session)
    payload = feed.payload
    assert payload["version"] == web_export_service.FEED_VERSION
    assert payload["job_count"] == 0
    assert payload["jobs"] == []
    assert "generated_at" in payload  # always stamped


def test_feed_contains_only_eligible_relevant_jobs(session: Session) -> None:
    _company(session)
    # Stage-1 survivor (eligible True) + unscreened (None, kept) + hard-dropped.
    _job(session, "keep-1", "Mechanical Engineering Intern", eligible=True)
    _job(session, "keep-2", "Mechanical Design Co-op", eligible=None)
    _job(session, "drop-1", "Senior Mechanical Engineer", eligible=False)
    session.flush()

    feed = web_export_service.build_feed(session)
    ids = {j["job_id"] for j in feed.payload["jobs"]}

    # The hard-dropped job must never reach the website.
    assert "drop-1" not in ids
    assert {"keep-1", "keep-2"} <= ids
    assert feed.payload["job_count"] == len(feed.payload["jobs"])


def test_feed_job_carries_display_fields(session: Session) -> None:
    _company(session)
    _job(session, "keep-1", "Mechanical Engineering Intern", eligible=True)
    session.flush()

    job = web_export_service.build_feed(session).payload["jobs"][0]
    for field in (
        "title",
        "company",
        "location",
        "fit_score",
        "application_url",
        "source_url",
        "term_season",
        "duration_label",
    ):
        assert field in job
    assert job["company"] == "Acme Robotics"
    assert job["application_url"].endswith("keep-1")


def test_export_web_writes_valid_json_file(session: Session, tmp_path) -> None:
    _company(session)
    _job(session, "keep-1", "Mechanical Engineering Intern", eligible=True)
    session.flush()

    out = tmp_path / "data" / "jobs.json"
    path = web_export_service.export_web(session, output=str(out))

    assert path.exists()
    parsed = json.loads(path.read_text(encoding="utf-8"))
    assert parsed["job_count"] == 1
    assert parsed["jobs"][0]["job_id"] == "keep-1"


def test_export_web_writes_empty_feed_without_crashing(session: Session, tmp_path) -> None:
    out = tmp_path / "data" / "jobs.json"
    path = web_export_service.export_web(session, output=str(out))
    parsed = json.loads(path.read_text(encoding="utf-8"))
    assert parsed["jobs"] == []
