"""Tests for the throttled, continue-on-error bulk AI pass (FEAT-003).

All provider interaction uses injected fakes - NO live network.
"""

from __future__ import annotations

import json

from sqlalchemy.orm import Session

from jobtracker.ai.provider import AIProvider, AIProviderError, AIResult, NullProvider
from jobtracker.db.models import AiAnalysisCache, Company, Job
from jobtracker.services import ai_service
from jobtracker.settings import Settings

VALID_RESULT = {
    "relevance_score": 80,
    "recommendation": "apply",
    "mechanical_relevance": "strong",
    "term_assessment": "fits",
    "duration_assessment": "fits",
    "location_assessment": "fits",
    "work_authorization_assessment": "Canadian OK",
    "matched_skills": ["CAD"],
    "missing_skills": [],
    "concerns": [],
    "evidence": ["mechanical systems"],
    "explanation": "Great fit.",
    "confidence": 0.9,
}


class FakeProvider(AIProvider):
    """Returns canned valid JSON and counts calls (no network)."""

    name = "fake"

    def __init__(self) -> None:
        self.calls = 0

    def analyze(self, prompt: str) -> AIResult:
        self.calls += 1
        return AIResult(
            text=json.dumps(VALID_RESULT),
            available=True,
            provider=self.name,
            model="fake-1",
            total_tokens=30,
        )


class FailingOnceProvider(AIProvider):
    """Raises AIProviderError for a specific job id, succeeds otherwise."""

    name = "fake-failing"

    def __init__(self, fail_for_title: str) -> None:
        self.fail_for_title = fail_for_title
        self.calls = 0

    def analyze(self, prompt: str) -> AIResult:
        self.calls += 1
        if self.fail_for_title in prompt:
            raise AIProviderError("rate limited (429)")
        return AIResult(
            text=json.dumps(VALID_RESULT),
            available=True,
            provider=self.name,
            model="fake-1",
            total_tokens=30,
        )


def _settings() -> Settings:
    return Settings(user_file="config/user.yaml")


def _seed(session: Session) -> None:
    session.add(Company(id="acme", name="Acme", ats="greenhouse"))
    # Two Stage-1 survivors (eligible True / None), one hard-dropped (False).
    session.add(
        Job(
            job_id="job-1",
            company_id="acme",
            source_job_id="1",
            title="Mechanical Engineering Intern One",
            description="Design mechanical systems.",
            description_hash="h1",
            status="active",
            eligible=True,
        )
    )
    session.add(
        Job(
            job_id="job-2",
            company_id="acme",
            source_job_id="2",
            title="Mechanical Engineering Intern Two",
            description="Build prototypes.",
            description_hash="h2",
            status="active",
            eligible=None,
        )
    )
    session.add(
        Job(
            job_id="job-3",
            company_id="acme",
            source_job_id="3",
            title="Dropped US Citizen Role",
            description="US citizens only.",
            description_hash="h3",
            status="active",
            eligible=False,
        )
    )
    session.flush()


def test_bulk_analyzes_only_survivors(session: Session) -> None:
    _seed(session)
    provider = FakeProvider()
    summary = ai_service.analyze_jobs_bulk(
        session, provider=provider, settings=_settings(), delay_seconds=0
    )
    # job-3 (eligible=False) is never considered or analyzed.
    assert summary.considered == 2
    assert summary.analyzed == 2
    assert provider.calls == 2
    assert session.query(AiAnalysisCache).count() == 2


def test_bulk_only_missing_skips_cached(session: Session) -> None:
    _seed(session)
    provider = FakeProvider()
    # Prime the cache for job-1.
    ai_service.analyze_job(
        session, "job-1", provider=provider, settings=_settings()
    )
    assert provider.calls == 1

    summary = ai_service.analyze_jobs_bulk(
        session,
        provider=provider,
        settings=_settings(),
        only_missing=True,
        delay_seconds=0,
    )
    assert summary.considered == 2
    assert summary.cached_skipped == 1
    assert summary.analyzed == 1  # only job-2 newly analyzed
    assert provider.calls == 2


def test_bulk_respects_limit(session: Session) -> None:
    _seed(session)
    provider = FakeProvider()
    summary = ai_service.analyze_jobs_bulk(
        session,
        provider=provider,
        settings=_settings(),
        limit=1,
        delay_seconds=0,
    )
    assert summary.analyzed == 1
    assert provider.calls == 1


def test_bulk_continues_past_provider_error(session: Session) -> None:
    _seed(session)
    provider = FailingOnceProvider(fail_for_title="Intern One")
    summary = ai_service.analyze_jobs_bulk(
        session, provider=provider, settings=_settings(), delay_seconds=0
    )
    # One job raised, the batch still completed the other.
    assert summary.errors == 1
    assert summary.analyzed == 1
    assert summary.considered == 2


def test_bulk_null_provider_returns_unavailable(session: Session) -> None:
    _seed(session)
    summary = ai_service.analyze_jobs_bulk(
        session, provider=NullProvider(), settings=_settings(), delay_seconds=0
    )
    assert summary.unavailable is True
    assert summary.analyzed == 0
    assert summary.considered == 0
    assert session.query(AiAnalysisCache).count() == 0
