"""AI relevance analysis service (SPEC Phase 3).

Owns the business logic for the AI relevance engine so the CLI and Streamlit
layers stay thin:

* resolves the user profile + ``profile_version`` (per-user or the config
  single-user profile),
* computes the cache key ``(job_id, description_hash, profile_version)`` and
  returns a still-valid cached :class:`~jobtracker.db.models.AiAnalysisCache`
  row when present,
* otherwise builds the prompt (cleaned description only), calls the configured
  provider, parses + validates the strict-JSON SPEC fields (robust to
  code-fence wrapping), stores the result + confidence + token usage, and logs
  cache hits/misses,
* degrades gracefully with a :class:`NullProvider`: returns a clear
  "AI unavailable - add a Gemini API key" result object and never raises,
* supports a manual override of the AI classification.

Nothing here imports Streamlit.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from jobtracker.ai.prompts import SPEC_FIELDS, JobPromptInput, build_analysis_prompt
from jobtracker.ai.provider import AIProvider, AIProviderError, get_provider
from jobtracker.config.user_loader import load_user_profile
from jobtracker.db.models import AiAnalysisCache, Job
from jobtracker.services import user_service
from jobtracker.settings import Settings, get_settings

logger = logging.getLogger("jobtracker.services.ai")

#: Allowed values for the SPEC ``recommendation`` field.
_RECOMMENDATIONS = ("apply", "maybe", "skip")

_CODE_FENCE_RE = re.compile(r"^\s*```(?:json)?\s*(.*?)\s*```\s*$", re.DOTALL)


class AiServiceError(ValueError):
    """Raised when the model output cannot be parsed/validated."""


@dataclass
class AnalysisResult:
    """Presentation-ready AI analysis outcome returned to callers.

    ``available`` is False only in the no-key degraded path; ``cache_hit`` is
    True when a valid cached row was reused (no provider call). ``result`` holds
    the parsed SPEC fields (empty when unavailable). ``overridden`` reflects a
    stored manual override.
    """

    job_id: str
    available: bool
    cache_hit: bool
    result: dict
    confidence: float | None = None
    provider: str = ""
    model: str = ""
    message: str | None = None
    overridden: bool = False
    total_tokens: int | None = None


# --- Profile resolution ----------------------------------------------------


def resolve_profile(
    session: Session,
    *,
    user_id: int | None = None,
    settings: Settings | None = None,
) -> tuple[dict, int]:
    """Return ``(profile_data, profile_version)`` for the cache key.

    For a logged-in ``user_id`` the stored per-user profile is used; otherwise
    the single-user ``config/user.yaml`` profile is loaded (its
    ``profile_version`` field drives invalidation).
    """
    if user_id is not None:
        profile = user_service.get_profile(session, user_id)
        if profile is not None:
            data = dict(profile.data or {})
            version = int(profile.profile_version or 1)
            return data, version
    settings = settings or get_settings()
    config_profile = load_user_profile(settings.user_file)
    return config_profile.model_dump(), int(config_profile.profile_version or 1)


def _job_prompt_input(job: Job, company_name: str) -> JobPromptInput:
    duration = job.duration_label or (
        f"{job.duration_months} months" if job.duration_months else "unknown"
    )
    return JobPromptInput(
        title=job.title or "",
        company=company_name,
        location=job.location or "",
        term_season=job.term_season or "unknown",
        duration_label=duration,
        citizenship_requirement=job.citizenship_requirement or "",
        work_authorization_requirement=job.work_authorization_requirement or "",
        visa_sponsorship=job.visa_sponsorship or "unknown",
        sponsorship_evidence=job.sponsorship_evidence or "",
        description=job.description or "",
        requirements=list(job.requirements or []),
        preferred_qualifications=list(job.preferred_qualifications or []),
        skills=list(job.skills or []),
        application_url=job.application_url or "",
    )


# --- Strict-JSON parsing ---------------------------------------------------


def strip_code_fences(text: str) -> str:
    """Strip an optional ```/```json code fence wrapping JSON output."""
    match = _CODE_FENCE_RE.match(text or "")
    if match:
        return match.group(1).strip()
    return (text or "").strip()


def parse_strict_json(text: str) -> dict:
    """Parse + validate the model's strict-JSON output into SPEC fields.

    Robust to code-fence wrapping. Raises :class:`AiServiceError` on malformed
    JSON, a non-object payload, missing SPEC fields, or an invalid
    ``recommendation`` value.
    """
    cleaned = strip_code_fences(text)
    if not cleaned:
        raise AiServiceError("Empty AI response.")
    try:
        data = json.loads(cleaned)
    except json.JSONDecodeError as exc:
        raise AiServiceError(f"AI response was not valid JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise AiServiceError("AI response JSON must be an object.")

    missing = [field for field in SPEC_FIELDS if field not in data]
    if missing:
        raise AiServiceError(f"AI response missing required fields: {missing}")

    recommendation = str(data.get("recommendation", "")).strip().lower()
    if recommendation not in _RECOMMENDATIONS:
        raise AiServiceError(
            f"Invalid recommendation '{data.get('recommendation')}'; "
            f"expected one of {_RECOMMENDATIONS}."
        )
    data["recommendation"] = recommendation
    return data


def _coerce_confidence(data: dict) -> float | None:
    try:
        return float(data.get("confidence"))
    except (TypeError, ValueError):
        return None


# --- Cache lookup / persistence -------------------------------------------


def _find_cache(
    session: Session, job_id: str, description_hash: str | None, profile_version: int
) -> AiAnalysisCache | None:
    return session.scalars(
        select(AiAnalysisCache).where(
            AiAnalysisCache.job_id == job_id,
            AiAnalysisCache.description_hash == description_hash,
            AiAnalysisCache.profile_version == profile_version,
        )
    ).first()


def _to_result(row: AiAnalysisCache, *, cache_hit: bool) -> AnalysisResult:
    return AnalysisResult(
        job_id=row.job_id or "",
        available=True,
        cache_hit=cache_hit,
        result=dict(row.result or {}),
        confidence=row.confidence,
        provider=row.provider or "",
        model=row.model or "",
        overridden=bool(row.overridden),
        total_tokens=row.total_tokens,
    )


def _unavailable_result(job_id: str, message: str) -> AnalysisResult:
    return AnalysisResult(
        job_id=job_id,
        available=False,
        cache_hit=False,
        result={},
        provider="null",
        message=message,
    )


def get_cached(
    session: Session,
    job_id: str,
    *,
    user_id: int | None = None,
    settings: Settings | None = None,
) -> AnalysisResult | None:
    """Return the cached analysis for a job if still valid, else None."""
    job = session.get(Job, job_id)
    if job is None:
        return None
    _, profile_version = resolve_profile(session, user_id=user_id, settings=settings)
    row = _find_cache(session, job_id, job.description_hash, profile_version)
    if row is None:
        return None
    return _to_result(row, cache_hit=True)


# --- Public analyze entry point -------------------------------------------


def analyze_job(
    session: Session,
    job_id: str,
    *,
    user_id: int | None = None,
    provider: AIProvider | None = None,
    settings: Settings | None = None,
    force: bool = False,
) -> AnalysisResult:
    """Analyze ``job_id`` against the resolved user profile.

    Returns a cached result when one exists for the current
    ``(job_id, description_hash, profile_version)`` key (unless ``force``).
    Otherwise calls the provider, parses the strict JSON, stores the result, and
    returns it. With a :class:`NullProvider` (no key) returns an informative
    unavailable result rather than raising.
    """
    settings = settings or get_settings()
    job = session.get(Job, job_id)
    if job is None:
        raise AiServiceError(f"Unknown job_id '{job_id}'.")

    profile_data, profile_version = resolve_profile(
        session, user_id=user_id, settings=settings
    )

    if not force:
        cached = _find_cache(session, job_id, job.description_hash, profile_version)
        if cached is not None:
            logger.info("AI cache hit for job %s (version %s)", job_id, profile_version)
            return _to_result(cached, cache_hit=True)

    logger.info("AI cache miss for job %s (version %s)", job_id, profile_version)
    active_provider = provider or get_provider(settings)

    if not active_provider.available:
        probe = active_provider.analyze("")
        return _unavailable_result(job_id, probe.message or "AI unavailable.")

    company_name = job.company_id
    prompt = build_analysis_prompt(profile_data, _job_prompt_input(job, company_name))

    try:
        raw = active_provider.analyze(prompt)
    except AIProviderError as exc:
        logger.warning("AI provider error for job %s: %s", job_id, exc)
        return _unavailable_result(
            job_id, f"AI provider error: {exc}. Check your Gemini API key."
        )

    if not raw.available:
        return _unavailable_result(job_id, raw.message or "AI unavailable.")

    parsed = parse_strict_json(raw.text)
    confidence = _coerce_confidence(parsed)

    now = datetime.now(UTC)
    row = _find_cache(session, job_id, job.description_hash, profile_version)
    if row is None:
        row = AiAnalysisCache(
            job_id=job_id,
            description_hash=job.description_hash,
            profile_version=profile_version,
            created_at=now,
        )
        session.add(row)
    row.result = parsed
    row.confidence = confidence
    row.provider = raw.provider
    row.model = raw.model
    row.prompt_tokens = raw.prompt_tokens
    row.completion_tokens = raw.completion_tokens
    row.total_tokens = raw.total_tokens
    row.overridden = False
    row.updated_at = now
    session.flush()

    # Keep the latest cached result pointer on the Job for quick read access.
    job.ai_analysis = parsed

    logger.info(
        "AI analysis stored for job %s (provider=%s model=%s tokens=%s)",
        job_id,
        raw.provider,
        raw.model,
        raw.total_tokens,
    )
    return _to_result(row, cache_hit=False)


def override_analysis(
    session: Session,
    job_id: str,
    result: dict,
    *,
    user_id: int | None = None,
    settings: Settings | None = None,
) -> AnalysisResult:
    """Manually override the AI classification for a job.

    Validates the supplied ``result`` against the SPEC fields and stores it as
    the cached analysis (marked ``overridden``) for the current cache key.
    """
    settings = settings or get_settings()
    job = session.get(Job, job_id)
    if job is None:
        raise AiServiceError(f"Unknown job_id '{job_id}'.")

    # Validate via the same strict-JSON rules so overrides stay well-formed.
    validated = parse_strict_json(json.dumps(result))
    _, profile_version = resolve_profile(session, user_id=user_id, settings=settings)

    now = datetime.now(UTC)
    row = _find_cache(session, job_id, job.description_hash, profile_version)
    if row is None:
        row = AiAnalysisCache(
            job_id=job_id,
            description_hash=job.description_hash,
            profile_version=profile_version,
            created_at=now,
        )
        session.add(row)
    row.result = validated
    row.confidence = _coerce_confidence(validated)
    row.provider = "manual"
    row.model = "override"
    row.overridden = True
    row.updated_at = now
    session.flush()
    job.ai_analysis = validated
    return _to_result(row, cache_hit=False)
