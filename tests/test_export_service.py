"""Tests for the export service: each format carries the required SPEC fields."""

from __future__ import annotations

import csv
import io
import json

from sqlalchemy.orm import Session

from jobtracker.db.models import Company, Job
from jobtracker.schemas.user_profile import UserProfile
from jobtracker.services import export_service


def _profile() -> UserProfile:
    return UserProfile(
        name="Jordan",
        program="Mechanical Engineering",
        citizenship=["Canadian"],
        work_authorization=["Canada"],
        available_terms=["Spring"],
        preferred_duration_months=4,
        interests=["robotics"],
        skills=["CAD", "SolidWorks"],
    )


def _seed_job(session: Session) -> None:
    session.add(Company(id="acme", name="Acme", ats="greenhouse"))
    session.add(
        Job(
            job_id="job-1",
            company_id="acme",
            source_job_id="1",
            title="Mechanical Engineering Intern",
            location="Toronto, Ontario, Canada",
            term_season="spring",
            duration_months=4,
            duration_label="4-month",
            citizenship_requirement="None stated",
            visa_sponsorship="unknown",
            application_url="https://apply.example/1",
            fit_score=92,
            description="Design mechanical systems and prototypes.",
            requirements=["Pursuing a mechanical engineering degree"],
            preferred_qualifications=["SolidWorks experience"],
            skills=["CAD", "SolidWorks"],
            status="active",
        )
    )
    session.flush()


def test_markdown_contains_required_fields(session: Session) -> None:
    _seed_job(session)
    body = export_service.render(session, "markdown", profile=_profile())
    for needle in (
        "Acme",
        "Mechanical Engineering Intern",
        "Toronto, Ontario, Canada",
        "spring",
        "4-month",
        "unknown",  # sponsorship status
        "https://apply.example/1",
        "92",
        "Design mechanical systems",
        "Pursuing a mechanical engineering degree",
        "SolidWorks experience",
        "CAD",
        "## User Profile",
        "Jordan",
    ):
        assert needle in body, needle


def test_json_contains_required_fields(session: Session) -> None:
    _seed_job(session)
    body = export_service.render(session, "json", profile=_profile())
    payload = json.loads(body)
    assert "user_profile" in payload
    assert payload["user_profile"]["name"] == "Jordan"
    job = payload["jobs"][0]
    for field in export_service.EXPORT_FIELDS:
        assert field in job
    assert job["company"] == "Acme"
    assert job["application_url"] == "https://apply.example/1"
    assert job["fit_score"] == 92
    assert job["requirements"] == ["Pursuing a mechanical engineering degree"]


def test_csv_contains_required_fields(session: Session) -> None:
    _seed_job(session)
    body = export_service.render(session, "csv", profile=_profile())
    rows = list(csv.reader(io.StringIO(body)))
    # Row 0 is the user-profile summary, row 1 the header, row 2 the first job.
    assert rows[0][0] == "# user_profile"
    assert "Jordan" in rows[0][1]
    assert list(export_service.EXPORT_FIELDS) == rows[1]
    data = dict(zip(rows[1], rows[2], strict=True))
    assert data["company"] == "Acme"
    assert data["title"] == "Mechanical Engineering Intern"
    assert data["application_url"] == "https://apply.example/1"
    assert data["fit_score"] == "92"
    assert "CAD" in data["skills"]
