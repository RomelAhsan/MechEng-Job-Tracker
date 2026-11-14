"""0-100 weighted fit score with hard filters (design 9, SPEC "SCREENING").

Pure function over a :class:`CanonicalJob` and a :class:`UserProfile`. Weights
come straight from the SPEC:

* Mechanical relevance: 30
* Co-op/internship type: 15
* Spring term match: 15
* Duration match: 10
* Location eligibility: 10
* Citizenship/sponsorship eligibility: 10
* Skill/interest alignment: 10

Hard filters (removed/expired, incompatible term/duration/location/citizenship)
zero out eligibility but are surfaced as *concerns* -- never hidden from the All
Internships view. Every result carries score + matched + concerns + missing +
evidence + confidence.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from jobtracker.extract.term import normalize_profile_term, term_set
from jobtracker.extract.work_auth import (
    ELIGIBLE,
    INELIGIBLE_ITAR,
    LIKELY_INELIGIBLE_SPONSORSHIP_REQUIRED,
    POSSIBLY_ELIGIBLE_UNKNOWN,
)
from jobtracker.schemas.job import (
    CanonicalJob,
    Classification,
    EmploymentType,
    JobStatus,
)
from jobtracker.schemas.user_profile import UserProfile

WEIGHT_MECHANICAL = 30
WEIGHT_TYPE = 15
WEIGHT_TERM = 15
WEIGHT_DURATION = 10
WEIGHT_LOCATION = 10
WEIGHT_CITIZENSHIP = 10
WEIGHT_SKILL = 10


@dataclass
class FitScoreResult:
    """A fit-score outcome with full evidence (SPEC "Every result")."""

    score: int = 0
    eligible: bool = True
    matched: list[str] = field(default_factory=list)
    concerns: list[str] = field(default_factory=list)
    missing: list[str] = field(default_factory=list)
    evidence: list[str] = field(default_factory=list)
    confidence: float = 0.0


_CLASSIFICATION_WEIGHT = {
    Classification.STRONG_MATCH.value: 1.0,
    Classification.POSSIBLE_MATCH.value: 0.6,
    Classification.WEAK_MATCH.value: 0.2,
    Classification.NOT_RELEVANT.value: 0.0,
}


def _norm(values: list[str]) -> set[str]:
    return {v.strip().lower() for v in values if v and v.strip()}


def _score_mechanical(job: CanonicalJob, result: FitScoreResult) -> int:
    weight = _CLASSIFICATION_WEIGHT.get(job.classification or "", 0.0)
    points = round(WEIGHT_MECHANICAL * weight)
    if weight >= 1.0:
        result.matched.append("Strong mechanical-engineering relevance.")
    elif weight > 0:
        result.matched.append("Partial mechanical-engineering relevance.")
    else:
        result.concerns.append("Not classified as mechanical-relevant.")
    return points


def _score_type(job: CanonicalJob, result: FitScoreResult) -> int:
    if job.employment_type in (EmploymentType.CO_OP.value, EmploymentType.INTERNSHIP.value):
        result.matched.append(f"Employment type is {job.employment_type}.")
        return WEIGHT_TYPE
    if job.employment_type == EmploymentType.UNKNOWN.value:
        result.missing.append("Employment type unknown.")
        return 0
    result.concerns.append(f"Employment type {job.employment_type} is not co-op/internship.")
    return 0


def _score_term(
    job: CanonicalJob, profile: UserProfile, result: FitScoreResult
) -> tuple[int, bool]:
    available = {
        t for t in (normalize_profile_term(x) for x in profile.available_terms) if t
    }
    job_terms = term_set(job.term_season)
    if not job_terms:
        result.missing.append("Term season unknown.")
        return 0, True  # unknown never hard-fails
    if job_terms & available:
        result.matched.append(f"Term matches availability ({job.term_season}).")
        return WEIGHT_TERM, True
    result.concerns.append(
        f"Term {job.term_season} is incompatible with availability."
    )
    return 0, False


def _score_duration(
    job: CanonicalJob, profile: UserProfile, result: FitScoreResult
) -> tuple[int, bool]:
    if job.duration_months is None:
        result.missing.append("Duration unknown.")
        return 0, True
    minimum = profile.minimum_duration_months
    maximum = profile.maximum_duration_months
    if minimum is not None and job.duration_months < minimum:
        result.concerns.append(
            f"Duration {job.duration_months}mo below minimum {minimum}mo."
        )
        return 0, False
    if maximum is not None and job.duration_months > maximum:
        result.concerns.append(
            f"Duration {job.duration_months}mo above maximum {maximum}mo."
        )
        return 0, False
    result.matched.append(f"Duration {job.duration_months}mo fits availability.")
    return WEIGHT_DURATION, True


def _score_location(
    job: CanonicalJob, profile: UserProfile, result: FitScoreResult
) -> tuple[int, bool]:
    country = (job.country or "").strip().lower()
    if not country:
        result.missing.append("Country unknown.")
        return 0, True
    preferred = _norm(profile.preferred_locations) | {"canada", "ontario"}
    if country == "canada":
        result.matched.append("Located in Canada (preferred).")
        return WEIGHT_LOCATION, True
    accept_sponsor = _norm(profile.acceptable_locations_with_sponsorship)
    if country in {"united states", "usa", "us"} and (
        "united states" in accept_sponsor
    ):
        # Location acceptable only with sponsorship -> handled by citizenship.
        result.matched.append("U.S. location acceptable with sponsorship.")
        return WEIGHT_LOCATION // 2, True
    if country in preferred:
        result.matched.append("Located in a preferred region.")
        return WEIGHT_LOCATION, True
    result.concerns.append(f"Location ({job.country}) outside preferences.")
    return 0, True


def _score_citizenship(job: CanonicalJob, result: FitScoreResult) -> tuple[int, bool]:
    status = job.eligibility_status
    if job.sponsorship_evidence:
        result.evidence.append(job.sponsorship_evidence)
    if status == ELIGIBLE:
        result.matched.append("Eligible under citizenship/sponsorship rules.")
        return WEIGHT_CITIZENSHIP, True
    if status == INELIGIBLE_ITAR:
        result.concerns.append("Ineligible: ITAR / US-citizen-only role.")
        return 0, False
    if status == LIKELY_INELIGIBLE_SPONSORSHIP_REQUIRED:
        result.concerns.append("Likely ineligible: sponsorship required/unavailable.")
        return 0, False
    if status == POSSIBLY_ELIGIBLE_UNKNOWN or status is None:
        result.missing.append("Citizenship/sponsorship eligibility unknown.")
        return WEIGHT_CITIZENSHIP // 2, True
    result.missing.append("Citizenship/sponsorship eligibility unknown.")
    return WEIGHT_CITIZENSHIP // 2, True


def _score_skill(
    job: CanonicalJob, profile: UserProfile, result: FitScoreResult
) -> int:
    job_tokens = _norm(list(job.skills))
    profile_tokens = _norm(list(profile.skills) + list(profile.interests))
    overlap = job_tokens & profile_tokens
    if not profile_tokens or not job_tokens:
        result.missing.append("No skill/interest overlap data.")
        return 0
    ratio = len(overlap) / len(profile_tokens)
    points = round(WEIGHT_SKILL * min(1.0, ratio))
    if overlap:
        result.matched.append("Skill/interest overlap: " + ", ".join(sorted(overlap)))
    return points


def score(job: CanonicalJob, profile: UserProfile) -> FitScoreResult:
    """Compute the 0-100 fit score for ``job`` against ``profile``.

    Hard filters zero eligibility (``eligible = False``) and record a concern,
    but the numeric breakdown is still returned so the job remains visible in
    All Internships.
    """
    result = FitScoreResult()
    eligible = True

    # Hard filter: removed/expired.
    if job.status in (JobStatus.REMOVED.value, JobStatus.EXPIRED.value):
        eligible = False
        result.concerns.append(f"Job is {job.status}.")

    total = 0
    total += _score_mechanical(job, result)
    total += _score_type(job, result)

    term_points, term_ok = _score_term(job, profile, result)
    total += term_points
    eligible = eligible and term_ok

    dur_points, dur_ok = _score_duration(job, profile, result)
    total += dur_points
    eligible = eligible and dur_ok

    loc_points, loc_ok = _score_location(job, profile, result)
    total += loc_points
    eligible = eligible and loc_ok

    cit_points, cit_ok = _score_citizenship(job, result)
    total += cit_points
    eligible = eligible and cit_ok

    total += _score_skill(job, profile, result)

    result.score = max(0, min(100, total))
    result.eligible = eligible
    result.confidence = round(job.classification_confidence or 0.0, 2)
    if job.sponsorship_evidence and job.sponsorship_evidence not in result.evidence:
        result.evidence.append(job.sponsorship_evidence)
    return result
