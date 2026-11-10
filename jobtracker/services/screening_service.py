"""Screening orchestration: classify + extract + score over job rows (design 9).

Thin service that wires the pure functions in ``jobtracker.extract`` and
``jobtracker.screen`` over persisted :class:`Job` rows and writes the
classification, extraction, and fit-score fields back onto each row. The active
profile comes from ``config/user.yaml`` (single implicit Phase-1 user).

All business logic stays here / in the pure-function layer -- callers (CLI,
dashboard) only invoke :func:`screen_jobs` / :func:`screen_job`.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from jobtracker.config.user_loader import load_user_profile
from jobtracker.db.models import Job
from jobtracker.extract.degree_experience import extract_degree_experience
from jobtracker.extract.term import extract_term
from jobtracker.extract.work_auth import extract_work_auth
from jobtracker.schemas.job import CanonicalJob
from jobtracker.schemas.user_profile import UserProfile
from jobtracker.screen.eligibility import compute_stage1_eligibility
from jobtracker.screen.fit_score import score as compute_fit_score
from jobtracker.screen.meche_classifier import classify
from jobtracker.settings import get_settings


@dataclass
class ScreeningSummary:
    """Aggregate outcome of a screening pass."""

    screened: int = 0
    strong: int = 0
    possible: int = 0
    weak: int = 0
    not_relevant: int = 0
    #: Count of jobs that passed the Stage-1 deterministic hard filter.
    eligible: int = 0


def _load_profile(profile: UserProfile | None) -> UserProfile:
    if profile is not None:
        return profile
    return load_user_profile(get_settings().user_file)


def _to_canonical(job: Job) -> CanonicalJob:
    """Build an in-memory CanonicalJob from a persisted row for scoring."""
    return CanonicalJob(
        job_id=job.job_id,
        source_job_id=job.source_job_id or "",
        company_id=job.company_id,
        title=job.title or "",
        description=job.description or "",
        location=job.location or "",
        country=job.country,
        remote_status=job.remote_status or "unknown",
        employment_type=job.employment_type or "unknown",
        skills=list(job.skills or []),
        status=job.status or "active",
    )


def screen_job(session: Session, job: Job, profile: UserProfile) -> None:
    """Classify, extract, and score a single row and persist the results."""
    title = job.title or ""
    description = job.description or ""

    # Mechanical relevance.
    classification = classify(title, description)
    job.classification = classification.classification.value
    job.classification_reasons = list(classification.classification_reasons)
    job.classification_confidence = classification.classification_confidence

    # Term / duration.
    term = extract_term(title, description)
    job.term_season = term.term_season.value
    job.term_start_date = term.term_start_date
    job.term_end_date = term.term_end_date
    job.duration_months = term.duration_months
    job.duration_label = term.duration_label

    # Citizenship / sponsorship.
    work_auth = extract_work_auth(description, country=job.country)
    job.citizenship_requirement = work_auth.citizenship_requirement
    job.work_authorization_requirement = work_auth.work_authorization_requirement
    job.visa_sponsorship = work_auth.visa_sponsorship.value
    job.sponsorship_confidence = work_auth.sponsorship_confidence
    job.sponsorship_evidence = work_auth.sponsorship_evidence
    job.eligibility_status = work_auth.eligibility_status

    # Degree-level / experience prerequisites (Stage-1 deterministic filter).
    degree_exp = extract_degree_experience(title, description)
    job.requires_advanced_degree = degree_exp.requires_advanced_or_completed_degree
    job.requires_senior_experience = degree_exp.requires_senior_experience
    job.min_years_experience = degree_exp.min_years_experience

    # Fit score over a canonical view that reflects the freshly extracted data.
    canonical = _to_canonical(job)
    canonical.classification = job.classification
    canonical.classification_confidence = job.classification_confidence
    canonical.term_season = job.term_season
    canonical.duration_months = job.duration_months
    canonical.eligibility_status = job.eligibility_status
    canonical.sponsorship_evidence = job.sponsorship_evidence

    fit = compute_fit_score(canonical, profile)
    job.fit_score = fit.score

    # Stage-1 deterministic hard-eligibility verdict (SEPARATE from fit_score):
    # a 100%-disqualifying job gets eligible=False and is removed everywhere the
    # user browses; unknowns stay eligible and survive to the AI stage.
    verdict = compute_stage1_eligibility(job, profile, degree_exp)
    job.eligible = verdict.eligible
    job.eligibility_reasons = list(verdict.reasons)
    job.eligibility_evidence = list(verdict.evidence)
    job.eligibility_failed_rules = list(verdict.failed_rules)


def screen_jobs(
    session: Session,
    *,
    job_ids: list[str] | None = None,
    profile: UserProfile | None = None,
) -> ScreeningSummary:
    """Screen all (or the given) job rows and persist results.

    When ``job_ids`` is ``None`` every job in the database is screened.
    """
    active_profile = _load_profile(profile)
    stmt = select(Job)
    if job_ids is not None:
        stmt = stmt.where(Job.job_id.in_(job_ids))

    summary = ScreeningSummary()
    for job in session.scalars(stmt):
        screen_job(session, job, active_profile)
        summary.screened += 1
        if job.classification == "strong_match":
            summary.strong += 1
        elif job.classification == "possible_match":
            summary.possible += 1
        elif job.classification == "weak_match":
            summary.weak += 1
        elif job.classification == "not_relevant":
            summary.not_relevant += 1
        if job.eligible:
            summary.eligible += 1
    return summary
