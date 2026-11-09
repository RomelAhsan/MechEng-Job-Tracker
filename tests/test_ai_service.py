"""Tests for the Phase 3 AI relevance engine (mocked provider, no live network).

Covers: provider factory key-gating, cache hit vs miss, invalidation when the
profile version or description hash changes, strict-JSON parsing (including
code-fence stripping) with rejection of malformed JSON, NullProvider graceful
fallback, and export-for-external-AI content. A separate prompts test asserts
raw HTML is never sent.
"""

from __future__ import annotations

import json

import pytest
from sqlalchemy.orm import Session

from jobtracker.ai.prompts import JobPromptInput, build_analysis_prompt
from jobtracker.ai.provider import (
    AIProvider,
    AIResult,
    GeminiProvider,
    NullProvider,
    get_provider,
)
from jobtracker.db.models import AiAnalysisCache, Company, Job, User, UserProfile
from jobtracker.services import ai_service, export_service
from jobtracker.settings import Settings

VALID_RESULT = {
    "relevance_score": 88,
    "recommendation": "apply",
    "mechanical_relevance": "strong",
    "term_assessment": "spring fits",
    "duration_assessment": "4 months fits",
    "location_assessment": "Toronto preferred",
    "work_authorization_assessment": "Canadian OK",
    "matched_skills": ["CAD"],
    "missing_skills": ["GD&T"],
    "concerns": [],
    "evidence": ["Design mechanical systems"],
    "explanation": "Great mechanical fit for a Waterloo co-op.",
    "confidence": 0.9,
}


class MockProvider(AIProvider):
    """Provider that returns a canned text and counts calls (no network)."""

    name = "mock"

    def __init__(self, text: str) -> None:
        self.text = text
        self.calls = 0

    def analyze(self, prompt: str) -> AIResult:
        self.calls += 1
        return AIResult(
            text=self.text,
            available=True,
            provider=self.name,
            model="mock-1",
            prompt_tokens=10,
            completion_tokens=20,
            total_tokens=30,
        )


def _seed_job(session: Session, *, description_hash: str = "h1") -> None:
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
            description="Design mechanical systems and prototypes.",
            description_hash=description_hash,
            requirements=["Pursuing a mechanical engineering degree"],
            preferred_qualifications=["SolidWorks experience"],
            skills=["CAD", "SolidWorks"],
            application_url="https://apply.example/1",
            fit_score=92,
            status="active",
        )
    )
    session.flush()


def _seed_user_profile(session: Session, *, version: int = 1) -> int:
    user = User(email="u@example.com", role="admin")
    session.add(user)
    session.flush()
    session.add(
        UserProfile(
            user_id=user.id,
            profile_version=version,
            data={"name": "Jordan", "skills": ["CAD"], "profile_version": version},
        )
    )
    session.flush()
    return user.id


def _settings() -> Settings:
    return Settings(user_file="config/user.yaml")


# --- Provider factory -----------------------------------------------------


def test_get_provider_returns_null_without_key() -> None:
    assert isinstance(get_provider(Settings(ai_provider="gemini")), NullProvider)
    assert isinstance(
        get_provider(Settings(ai_provider=None, ai_api_key="AIzaFake")), NullProvider
    )


def test_get_provider_returns_gemini_when_configured() -> None:
    provider = get_provider(Settings(ai_provider="gemini", ai_api_key="AIzaFake"))
    assert isinstance(provider, GeminiProvider)


# --- Cache hit vs miss ----------------------------------------------------


def test_cache_miss_then_hit(session: Session) -> None:
    _seed_job(session)
    provider = MockProvider(json.dumps(VALID_RESULT))

    first = ai_service.analyze_job(
        session, "job-1", provider=provider, settings=_settings()
    )
    assert first.available is True
    assert first.cache_hit is False
    assert first.result["relevance_score"] == 88
    assert provider.calls == 1

    second = ai_service.analyze_job(
        session, "job-1", provider=provider, settings=_settings()
    )
    assert second.cache_hit is True
    assert provider.calls == 1  # provider NOT called again

    rows = session.query(AiAnalysisCache).all()
    assert len(rows) == 1
    assert rows[0].total_tokens == 30


def test_force_rerun_bypasses_cache(session: Session) -> None:
    _seed_job(session)
    provider = MockProvider(json.dumps(VALID_RESULT))
    ai_service.analyze_job(session, "job-1", provider=provider, settings=_settings())
    ai_service.analyze_job(
        session, "job-1", provider=provider, settings=_settings(), force=True
    )
    assert provider.calls == 2


# --- Invalidation ---------------------------------------------------------


def test_cache_invalidated_on_description_hash_change(session: Session) -> None:
    _seed_job(session, description_hash="h1")
    provider = MockProvider(json.dumps(VALID_RESULT))
    ai_service.analyze_job(session, "job-1", provider=provider, settings=_settings())
    assert provider.calls == 1

    job = session.get(Job, "job-1")
    job.description_hash = "h2"
    session.flush()

    ai_service.analyze_job(session, "job-1", provider=provider, settings=_settings())
    assert provider.calls == 2
    assert session.query(AiAnalysisCache).count() == 2


def test_cache_invalidated_on_profile_version_change(session: Session) -> None:
    _seed_job(session)
    user_id = _seed_user_profile(session, version=1)
    provider = MockProvider(json.dumps(VALID_RESULT))

    ai_service.analyze_job(
        session, "job-1", user_id=user_id, provider=provider, settings=_settings()
    )
    assert provider.calls == 1

    profile = session.query(UserProfile).filter_by(user_id=user_id).one()
    profile.profile_version = 2
    session.flush()

    ai_service.analyze_job(
        session, "job-1", user_id=user_id, provider=provider, settings=_settings()
    )
    assert provider.calls == 2


# --- Strict-JSON parsing --------------------------------------------------


def test_parse_strips_code_fences() -> None:
    fenced = "```json\n" + json.dumps(VALID_RESULT) + "\n```"
    parsed = ai_service.parse_strict_json(fenced)
    assert parsed["recommendation"] == "apply"


def test_parse_plain_json() -> None:
    parsed = ai_service.parse_strict_json(json.dumps(VALID_RESULT))
    assert parsed["relevance_score"] == 88


def test_parse_rejects_malformed_json() -> None:
    with pytest.raises(ai_service.AiServiceError):
        ai_service.parse_strict_json("not json {")


def test_parse_rejects_missing_fields() -> None:
    with pytest.raises(ai_service.AiServiceError):
        ai_service.parse_strict_json(json.dumps({"relevance_score": 5}))


def test_parse_rejects_bad_recommendation() -> None:
    bad = dict(VALID_RESULT, recommendation="definitely")
    with pytest.raises(ai_service.AiServiceError):
        ai_service.parse_strict_json(json.dumps(bad))


def test_analyze_with_fenced_response(session: Session) -> None:
    _seed_job(session)
    provider = MockProvider("```json\n" + json.dumps(VALID_RESULT) + "\n```")
    result = ai_service.analyze_job(
        session, "job-1", provider=provider, settings=_settings()
    )
    assert result.result["recommendation"] == "apply"


# --- NullProvider graceful fallback ---------------------------------------


def test_null_provider_graceful(session: Session) -> None:
    _seed_job(session)
    result = ai_service.analyze_job(
        session, "job-1", provider=NullProvider(), settings=_settings()
    )
    assert result.available is False
    assert "Gemini API key" in (result.message or "")
    # Nothing cached, no exception raised.
    assert session.query(AiAnalysisCache).count() == 0


# --- Manual override ------------------------------------------------------


def test_manual_override(session: Session) -> None:
    _seed_job(session)
    overridden = dict(VALID_RESULT, recommendation="skip", relevance_score=10)
    result = ai_service.override_analysis(
        session, "job-1", overridden, settings=_settings()
    )
    assert result.overridden is True
    cached = ai_service.get_cached(session, "job-1", settings=_settings())
    assert cached.result["recommendation"] == "skip"
    assert cached.overridden is True


# --- Export for external AI -----------------------------------------------


def test_export_for_external_ai_includes_spec_fields(session: Session) -> None:
    _seed_job(session)
    body = export_service.render_ai_export(
        session, "json", job_ids=["job-1"]
    )
    payload = json.loads(body)
    assert "user_profile" in payload
    job = payload["jobs"][0]
    for field in export_service.EXPORT_FIELDS:
        assert field in job
    assert job["company"] == "Acme"
    assert job["fit_score"] == 92
    assert job["application_url"] == "https://apply.example/1"


def test_export_for_external_ai_all_active(session: Session) -> None:
    _seed_job(session)
    body = export_service.render_ai_export(session, "markdown")
    assert "Mechanical Engineering Intern" in body
    assert "## User Profile" in body


# --- Prompt safety: never send raw HTML -----------------------------------


def test_prompt_never_contains_raw_html() -> None:
    job = JobPromptInput(
        title="Mech Intern",
        company="Acme",
        location="Toronto",
        term_season="spring",
        duration_label="4-month",
        citizenship_requirement="",
        work_authorization_requirement="",
        visa_sponsorship="unknown",
        sponsorship_evidence="",
        description="Design mechanical systems and prototypes.",
        requirements=["Pursuing ME degree"],
        preferred_qualifications=["SolidWorks"],
        skills=["CAD"],
        application_url="https://apply.example/1",
    )
    prompt = build_analysis_prompt({"name": "Jordan", "skills": ["CAD"]}, job)
    assert "Design mechanical systems" in prompt
    for tag in ("<div", "<p>", "<span", "<br", "</"):
        assert tag not in prompt
