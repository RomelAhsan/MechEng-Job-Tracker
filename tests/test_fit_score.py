"""Tests for the weighted fit score, hard filters, and screening service."""

from __future__ import annotations

from sqlalchemy.orm import Session

from jobtracker.db.models import Company, Job
from jobtracker.extract.work_auth import ELIGIBLE
from jobtracker.schemas.job import (
    CanonicalJob,
    Classification,
    EmploymentType,
    JobStatus,
    TermSeason,
)
from jobtracker.schemas.user_profile import UserProfile
from jobtracker.screen.fit_score import (
    WEIGHT_MECHANICAL,
    WEIGHT_TERM,
    WEIGHT_TYPE,
    score,
)
from jobtracker.services.screening_service import screen_jobs


def _profile() -> UserProfile:
    return UserProfile(
        citizenship=["Canadian"],
        work_authorization=["Canada"],
        preferred_locations=["Ontario", "Canada"],
        acceptable_locations_with_sponsorship=["United States"],
        available_terms=["Spring"],
        preferred_duration_months=4,
        minimum_duration_months=4,
        maximum_duration_months=4,
        interests=["robotics"],
        skills=["CAD", "SolidWorks"],
    )


def _strong_job(**overrides: object) -> CanonicalJob:
    data = dict(
        title="Mechanical Engineering Intern",
        classification=Classification.STRONG_MATCH.value,
        classification_confidence=0.95,
        employment_type=EmploymentType.CO_OP.value,
        term_season=TermSeason.SPRING.value,
        duration_months=4,
        country="Canada",
        eligibility_status=ELIGIBLE,
        skills=["CAD", "SolidWorks"],
        status=JobStatus.ACTIVE.value,
    )
    data.update(overrides)
    return CanonicalJob(**data)


def test_weighted_math_strong_job_high_score() -> None:
    result = score(_strong_job(), _profile())
    # mechanical 30 + type 15 + term 15 + duration 10 + location 10 +
    # citizenship 10 + skill (partial) => a near-perfect, fully eligible score.
    assert result.score >= 95
    assert result.eligible is True


def test_mechanical_weight_contribution() -> None:
    strong = score(_strong_job(classification=Classification.STRONG_MATCH.value), _profile())
    not_rel = score(
        _strong_job(classification=Classification.NOT_RELEVANT.value), _profile()
    )
    assert strong.score - not_rel.score == WEIGHT_MECHANICAL


def test_hard_filter_removed_zeros_eligibility() -> None:
    result = score(_strong_job(status=JobStatus.REMOVED.value), _profile())
    assert result.eligible is False
    # Surfaced as a concern, not hidden; score still computed.
    assert any("removed" in c for c in result.concerns)
    assert result.score > 0


def test_hard_filter_incompatible_term() -> None:
    result = score(_strong_job(term_season=TermSeason.FALL.value), _profile())
    assert result.eligible is False
    assert any("incompatible" in c.lower() for c in result.concerns)


def test_result_completeness() -> None:
    result = score(_strong_job(), _profile())
    for attr in ("score", "matched", "concerns", "missing", "evidence", "confidence"):
        assert hasattr(result, attr)
    assert isinstance(result.score, int)
    assert isinstance(result.confidence, float)


def test_type_contribution_full_time_zero() -> None:
    coop = score(_strong_job(employment_type=EmploymentType.CO_OP.value), _profile())
    ft = score(_strong_job(employment_type=EmploymentType.FULL_TIME.value), _profile())
    assert coop.score - ft.score == WEIGHT_TYPE
    assert ft.eligible is True  # employment type is a soft factor, not a hard filter


def test_term_contribution() -> None:
    with_term = score(_strong_job(term_season=TermSeason.SPRING.value), _profile())
    unknown_term = score(_strong_job(term_season=TermSeason.UNKNOWN.value), _profile())
    assert with_term.score - unknown_term.score == WEIGHT_TERM
    assert unknown_term.eligible is True  # unknown never hard-fails


def test_screening_service_persists_results(session: Session) -> None:
    session.add(Company(id="acme", name="Acme", ats="greenhouse"))
    session.add(
        Job(
            job_id="job-1",
            company_id="acme",
            source_job_id="1",
            title="Mechanical Engineering Intern",
            description=(
                "4-month Spring 2027 co-op in Toronto, Ontario. "
                "Experience with CAD and SolidWorks preferred."
            ),
            country="Canada",
            employment_type=EmploymentType.CO_OP.value,
            skills=["CAD", "SolidWorks"],
            status=JobStatus.ACTIVE.value,
        )
    )
    session.flush()

    summary = screen_jobs(session, profile=_profile())
    session.flush()

    assert summary.screened == 1
    assert summary.strong == 1
    job = session.get(Job, "job-1")
    assert job.classification == Classification.STRONG_MATCH.value
    assert job.term_season == TermSeason.SPRING.value
    assert job.duration_months == 4
    assert job.eligibility_status == ELIGIBLE
    assert job.fit_score is not None and job.fit_score > 0
