"""Tests for HTML cleaning, normalization, hashing, and the quarantine gate."""

from __future__ import annotations

import pytest

from jobtracker.connectors.base import RawJob
from jobtracker.normalize import clean_html, description_hash, normalize
from jobtracker.normalize.normalizer import (
    QuarantineError,
    infer_employment_type,
    infer_remote_status,
    split_location,
)
from jobtracker.schemas.company import CompanyConfig
from jobtracker.schemas.job import EmploymentType, RemoteStatus


def _company() -> CompanyConfig:
    return CompanyConfig(id="acme", name="Acme", ats="greenhouse", board_token="acme")


def _raw(**overrides) -> RawJob:
    base = dict(
        source_job_id="1",
        title="Mechanical Engineering Co-op",
        location="Toronto, ON, Canada",
        description_html=(
            "<h3>Requirements</h3><ul><li>SolidWorks and CAD</li>"
            "<li>GD&amp;T experience</li></ul><p>Hybrid thermal systems team.</p>"
            "<script>bad()</script>"
        ),
        application_url="https://jobs.example.com/acme/1/apply",
        source_url="https://jobs.example.com/acme/1",
    )
    base.update(overrides)
    return RawJob(**base)


def test_clean_html_strips_scripts_and_collapses():
    text = clean_html("<p>Hello   world</p><script>evil()</script>")
    assert "evil" not in text
    assert "Hello world" in text


def test_normalize_produces_canonical_fields():
    job = normalize(_raw(), _company())
    assert job.company_id == "acme"
    assert job.company_name == "Acme"
    assert job.city == "Toronto"
    assert job.province_state == "ON"
    assert job.country == "Canada"
    assert job.remote_status == RemoteStatus.HYBRID.value
    assert job.employment_type == EmploymentType.CO_OP.value
    assert "SolidWorks and CAD" in job.requirements
    assert "CAD" in job.skills
    assert "SolidWorks" in job.skills
    assert "GD&T" in job.skills
    assert job.application_url.endswith("/apply")
    assert job.source_type == "greenhouse"
    assert job.raw_payload is None or isinstance(job.raw_payload, dict)


def test_split_location_us_state():
    city, province, country = split_location("Austin, TX")
    assert city == "Austin"
    assert province == "TX"
    assert country == "United States"


def test_infer_remote_and_employment_defaults_unknown():
    assert infer_remote_status("Engineer", "Great job") == RemoteStatus.UNKNOWN
    assert (
        infer_employment_type("Engineer", "Great job", None)
        == EmploymentType.UNKNOWN
    )
    assert infer_remote_status("x", "Fully remote role") == RemoteStatus.REMOTE
    assert infer_employment_type("Intern", "", None) == EmploymentType.INTERNSHIP


def test_description_hash_is_stable():
    a = description_hash("desc", "title", "loc", ["r1", "r2"])
    b = description_hash("desc", "title", "loc", ["r1", "r2"])
    c = description_hash("desc", "title", "loc", ["r1"])
    assert a == b
    assert a != c
    assert len(a) == 64


def test_normalize_hash_matches_helper():
    job = normalize(_raw(), _company())
    recomputed = description_hash(
        job.description, job.title, job.location, job.requirements
    )
    assert job.description_hash == recomputed


def test_quarantine_on_missing_application_url():
    with pytest.raises(QuarantineError) as exc:
        normalize(_raw(application_url=None), _company())
    assert "application_url" in exc.value.reason


def test_quarantine_on_missing_source_url():
    with pytest.raises(QuarantineError) as exc:
        normalize(_raw(source_url=None), _company())
    assert "source_url" in exc.value.reason
