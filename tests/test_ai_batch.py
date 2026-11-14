"""Tests for BATCHED AI analysis (many jobs per provider request).

Batching is the efficiency fix: the Gemini free tier limits REQUESTS/day, so
one request must be able to assess many jobs. No live network -- a fake
provider returns a JSON array keyed by the echoed per-job ``index``.
"""

from __future__ import annotations

import json

import pytest
from sqlalchemy.orm import Session

from jobtracker.ai.prompts import JobPromptInput, build_batch_analysis_prompt
from jobtracker.ai.provider import AIProvider, AIResult
from jobtracker.db.models import AiAnalysisCache, Company, Job
from jobtracker.services import ai_service
from jobtracker.settings import Settings


def _verdict(index: int, recommendation: str = "apply") -> dict:
    return {
        "index": index,
        "relevance_score": 80,
        "recommendation": recommendation,
        "mechanical_relevance": "strong",
        "term_assessment": "fits",
        "duration_assessment": "fits",
        "location_assessment": "fits",
        "work_authorization_assessment": "Canadian OK",
        "matched_skills": ["CAD"],
        "missing_skills": [],
        "concerns": [],
        "evidence": ["mechanical systems"],
        "explanation": "Fits.",
        "confidence": 0.9,
    }


class BatchProvider(AIProvider):
    """Echoes a JSON array of verdicts for every job index in the prompt."""

    name = "batch-fake"

    def __init__(self, *, drop_indices: set[int] | None = None) -> None:
        self.calls = 0
        self.last_prompt = ""
        self.drop = drop_indices or set()

    def analyze(self, prompt: str) -> AIResult:
        self.calls += 1
        self.last_prompt = prompt
        # One verdict per "--- JOB n ---" marker, minus any we simulate missing.
        n = prompt.count("--- JOB ")
        arr = [_verdict(i) for i in range(n) if i not in self.drop]
        return AIResult(text=json.dumps(arr), available=True, provider=self.name)


def _settings(size: int = 12) -> Settings:
    return Settings(user_file="config/user.yaml", ai_batch_size=size)


def _seed(session: Session, count: int) -> None:
    session.add(Company(id="acme", name="Acme", ats="greenhouse"))
    for i in range(count):
        session.add(
            Job(
                job_id=f"job-{i}",
                company_id="acme",
                source_job_id=str(i),
                title=f"Mechanical Engineering Intern {i}",
                description="Design mechanical systems.",
                description_hash=f"h{i}",
                status="active",
                eligible=True,
            )
        )
    session.flush()


def test_batch_prompt_contains_all_jobs_and_asks_for_array() -> None:
    jobs = [
        JobPromptInput(
            title=f"Role {i}",
            company="Acme",
            location="Toronto",
            term_season="spring",
            duration_label="4-month",
            citizenship_requirement="",
            work_authorization_requirement="",
            visa_sponsorship="unknown",
            sponsorship_evidence="",
            description="desc",
            requirements=[],
            preferred_qualifications=[],
            skills=[],
            application_url="https://x",
        )
        for i in range(3)
    ]
    prompt = build_batch_analysis_prompt({"name": "J"}, jobs)
    assert prompt.count("--- JOB ") == 3
    assert "JSON ARRAY" in prompt
    assert "index" in prompt


def test_bulk_batches_many_jobs_into_few_requests(session: Session) -> None:
    _seed(session, 25)
    provider = BatchProvider()

    summary = ai_service.analyze_jobs_bulk(
        session, provider=provider, settings=_settings(size=12), delay_seconds=0
    )

    # 25 jobs / 12 per request = 3 requests (not 25).
    assert provider.calls == 3
    assert summary.analyzed == 25
    # Every job got a cached analysis.
    assert session.query(AiAnalysisCache).count() == 25


def test_batch_maps_results_back_by_index(session: Session) -> None:
    _seed(session, 3)
    provider = BatchProvider()
    ai_service.analyze_jobs_bulk(
        session, provider=provider, settings=_settings(size=12), delay_seconds=0
    )
    for i in range(3):
        row = (
            session.query(AiAnalysisCache)
            .filter(AiAnalysisCache.job_id == f"job-{i}")
            .one()
        )
        assert row.result["recommendation"] == "apply"


def test_batch_missing_result_is_retried_not_fatal(session: Session) -> None:
    _seed(session, 3)
    # The model omits index 1 from its response.
    provider = BatchProvider(drop_indices={1})

    summary = ai_service.analyze_jobs_bulk(
        session, provider=provider, settings=_settings(size=12), delay_seconds=0
    )

    assert summary.analyzed == 2
    assert summary.errors == 1
    # The omitted job has no cache row, so a later run will pick it up.
    assert (
        session.query(AiAnalysisCache)
        .filter(AiAnalysisCache.job_id == "job-1")
        .count()
        == 0
    )


def test_parse_strict_json_array_skips_malformed_elements() -> None:
    good = _verdict(0)
    text = json.dumps([good, {"index": 1}, {"not": "valid"}, "garbage"])
    parsed = ai_service.parse_strict_json_array(text)
    # Only the well-formed, index-carrying object survives.
    assert [p["index"] for p in parsed] == [0]


def test_parse_strict_json_array_rejects_non_array() -> None:
    with pytest.raises(ai_service.AiServiceError):
        ai_service.parse_strict_json_array(json.dumps({"index": 0}))


class VerifyProvider(BatchProvider):
    """Second-opinion fake: rejects the job indices in ``reject``."""

    def __init__(self, reject: set[int]) -> None:
        super().__init__()
        self.reject = reject

    def analyze(self, prompt: str) -> AIResult:
        self.calls += 1
        n = prompt.count("--- JOB ")
        arr = []
        for i in range(n):
            v = _verdict(i, "skip" if i in self.reject else "apply")
            v["failed_rules"] = [2] if i in self.reject else []
            arr.append(v)
        return AIResult(text=json.dumps(arr), available=True, provider="verify")


def test_second_opinion_must_agree_before_ai_job_is_shown(session: Session) -> None:
    from jobtracker.screen.ai_verdict import ai_eligibility

    _seed(session, 2)
    for job in session.query(Job):
        job.screening_tier = "ai"
    session.flush()
    settings = Settings(
        user_file="config/user.yaml",
        ai_batch_size=12,
        gemini_api_key="fake",
        gemini_verify_model="verify-model",
    )
    ai_service.analyze_jobs_batch(
        session, ["job-0", "job-1"], provider=BatchProvider(), settings=settings
    )
    rows = {r.job_id: r for r in session.query(AiAnalysisCache)}
    assert not ai_service.has_current_verification(rows["job-0"].result)

    verifier = VerifyProvider(reject={1})
    done = ai_service.verify_jobs(
        session, ["job-0", "job-1"], settings=settings, provider=verifier
    )
    assert done == 2 and verifier.calls == 1
    rows = {r.job_id: r for r in session.query(AiAnalysisCache)}
    assert ai_service.has_current_verification(rows["job-0"].result)
    assert ai_eligibility(rows["job-0"].result) is True
    assert ai_eligibility(rows["job-1"].result) is False

    # Already verified -> no new request.
    assert ai_service.verify_jobs(
        session, ["job-0", "job-1"], settings=settings, provider=verifier
    ) == 0


def test_parse_array_repairs_trailing_commas() -> None:
    text = json.dumps([_verdict(0)])[:-2] + ",}]"
    parsed = ai_service.parse_strict_json_array(text)
    assert parsed and parsed[0]["index"] == 0
