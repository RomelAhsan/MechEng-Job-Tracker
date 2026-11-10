"""Tests for the Stage-2 AI gate on 'Relevant For Me' (FEAT-003).

Verifies the never-empty fallback (no cache / no key / provider error keeps the
job) and that an explicit AI not-eligible verdict hides a survivor. Uses the
in-memory session and pre-seeded cache rows - NO live network.
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy.orm import Session

from jobtracker.db.models import AiAnalysisCache, Company, Job
from jobtracker.services import dashboard_service
from jobtracker.services.dashboard_service import apply_ai_eligibility_gate

ELIGIBLE_RESULT = {
    "recommendation": "apply",
    "work_authorization_assessment": "Canadian OK",
    "duration_assessment": "fits",
    "term_assessment": "fits",
}
NOT_ELIGIBLE_RESULT = {
    "recommendation": "skip",
    "work_authorization_assessment": "Requires US citizen; ineligible.",
    "duration_assessment": "fits",
    "term_assessment": "fits",
}


def _seed_survivor(session: Session, job_id: str) -> None:
    session.add(
        Job(
            job_id=job_id,
            company_id="acme",
            source_job_id=job_id,
            title="Mechanical Engineering Intern",
            description="Design mechanical systems.",
            description_hash=f"h-{job_id}",
            status="active",
            classification="strong_match",
            eligible=True,
        )
    )


def _seed(session: Session) -> None:
    session.add(Company(id="acme", name="Acme", ats="greenhouse"))
    _seed_survivor(session, "job-1")
    _seed_survivor(session, "job-2")
    session.flush()


def _add_cache(session: Session, job_id: str, result: dict) -> None:
    job = session.get(Job, job_id)
    session.add(
        AiAnalysisCache(
            job_id=job_id,
            description_hash=job.description_hash,
            profile_version=1,
            result=result,
            provider="fake",
            model="fake-1",
            created_at=datetime.now(UTC),
        )
    )
    session.flush()


# --- The full read path (no network: reads cached analyses only) -----------


def test_no_cache_equals_stage1_survivors(session: Session) -> None:
    _seed(session)
    views = dashboard_service.list_relevant_jobs(session)
    assert {v.job_id for v in views} == {"job-1", "job-2"}


def test_not_eligible_cache_hides_survivor(session: Session) -> None:
    _seed(session)
    _add_cache(session, "job-1", NOT_ELIGIBLE_RESULT)
    views = dashboard_service.list_relevant_jobs(session)
    assert {v.job_id for v in views} == {"job-2"}


def test_eligible_cache_keeps_survivor(session: Session) -> None:
    _seed(session)
    _add_cache(session, "job-1", ELIGIBLE_RESULT)
    _add_cache(session, "job-2", ELIGIBLE_RESULT)
    views = dashboard_service.list_relevant_jobs(session)
    assert {v.job_id for v in views} == {"job-1", "job-2"}


# --- The pure gate: provider-error / no-cache fallback ---------------------


def test_gate_keeps_jobs_without_cached_verdict() -> None:
    # None == no cached analysis (never run, no key, or prior provider error).
    kept = apply_ai_eligibility_gate(
        ["a", "b"], lambda _job_id: None
    )
    assert kept == ["a", "b"]


def test_gate_drops_only_not_eligible() -> None:
    verdicts = {"a": True, "b": False, "c": None}
    kept = apply_ai_eligibility_gate(
        ["a", "b", "c"], lambda job_id: verdicts[job_id]
    )
    assert kept == ["a", "c"]
