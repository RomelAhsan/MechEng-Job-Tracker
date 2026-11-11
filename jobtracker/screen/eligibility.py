"""Stage-1 deterministic hard-eligibility verdict (no AI, always runs).

A pure function over an already-screened job (ORM row or canonical view), the
single user profile, and the degree/experience extraction. It produces a single
persisted boolean verdict: a job that is 100% disqualifying for the user is
REMOVED entirely from the dashboard, while ambiguous UNKNOWNs survive to the
later AI stage (leniency rule).

This is SEPARATE from the 0-100 ``fit_score`` (which stays and is still shown):
``fit_score`` ranks, this verdict gates. The hard-exclude rules each set
``eligible=False`` with a human-readable reason, the exact evidence, and a
machine-readable rule id. ITAR / sponsorship are NOT re-derived here: the
already-persisted ``eligibility_status`` (from
:mod:`jobtracker.extract.work_auth`) is read directly.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from jobtracker.extract.degree_experience import DegreeExperienceExtraction
from jobtracker.extract.work_auth import (
    INELIGIBLE_ITAR,
    LIKELY_INELIGIBLE_SPONSORSHIP_REQUIRED,
)
from jobtracker.schemas.job import EmploymentType, JobStatus, TermSeason
from jobtracker.schemas.user_profile import UserProfile

#: Known employment types that are NOT co-op/internship. A KNOWN
#: non-co-op/internship type hard-fails; UNKNOWN never fails (leniency).
_DISQUALIFYING_EMPLOYMENT_TYPES: frozenset[str] = frozenset(
    {EmploymentType.FULL_TIME.value, EmploymentType.NEW_GRAD.value}
)

# Student-role signals. If ANY of these appear in the title/description the role
# is plausibly for a student and must NOT be hard-failed by the professional-
# title rule below -- it stays ambiguous and goes to the AI stage.
_STUDENT_SIGNAL_RE = re.compile(
    r"\b(?:intern(?:ship)?|co-?op|student|work\s+term|placement|"
    r"undergraduate|new\s*grad(?:uate)?|university|campus|"
    r"emerging\s+talent|early\s+career|apprentice(?:ship)?)\b",
    re.IGNORECASE,
)

# Clearly-professional role TITLES (full-time positions). These are matched
# against the TITLE ONLY, and only hard-fail when NO student signal appears
# anywhere -- so "Manufacturing Engineering Internship" (student) and a bare
# "Manufacturing Engineer" (professional) are treated differently. This catches
# the common case of a full-time posting whose title omits the word
# "full-time" (e.g. "Build Engineer", "Operations Engineer") that would
# otherwise sit in UNKNOWN and lean entirely on the quota-limited AI stage.
# Deliberately NARROW: it needs a definite professional-role noun; a vague or
# genuinely ambiguous title has no match and still passes to AI.
_PROFESSIONAL_TITLE_RE = re.compile(
    r"\b(?:"
    r"manager|director|head\s+of|vice\s+president|vp|chief|"
    r"lead\b|supervisor|administrator|coordinator|"
    r"accountant|controller|recruiter|"
    r"technician|operator|machinist|welder|fabricator"
    r")\b",
    re.IGNORECASE,
)


@dataclass
class EligibilityVerdict:
    """Outcome of the Stage-1 deterministic hard filter (evidence-carrying)."""

    eligible: bool = True
    reasons: list[str] = field(default_factory=list)
    evidence: list[str] = field(default_factory=list)
    #: Machine-readable ids of every rule that failed (empty when eligible).
    failed_rules: list[str] = field(default_factory=list)


def _norm(values: list[str] | None) -> set[str]:
    return {v.strip().lower() for v in (values or []) if v and v.strip()}


def compute_stage1_eligibility(
    job_or_canonical: object,
    profile: UserProfile,
    degree_exp: DegreeExperienceExtraction,
) -> EligibilityVerdict:
    """Apply the deterministic HARD-exclude rules and return the verdict.

    ``job_or_canonical`` is any object exposing the persisted screening fields
    (``status``, ``employment_type``, ``eligibility_status``, ``term_season``,
    ``duration_months``, ``citizenship_requirement``). Each failing rule sets
    ``eligible=False`` and records a reason, evidence, and a rule id. UNKNOWN
    employment_type / term / duration never fail (leniency). When no rule fires
    the job is eligible and survives to Stage 2.
    """
    verdict = EligibilityVerdict()
    job = job_or_canonical

    def _fail(rule: str, reason: str, evidence: str | None = None) -> None:
        verdict.eligible = False
        verdict.failed_rules.append(rule)
        verdict.reasons.append(reason)
        if evidence:
            verdict.evidence.append(evidence)

    # (1) removed / expired.
    status = getattr(job, "status", None)
    if status in (JobStatus.REMOVED.value, JobStatus.EXPIRED.value):
        _fail("removed", f"Posting is {status}.")

    # (2) employment_type is a KNOWN non-co-op/internship type. UNKNOWN passes.
    employment_type = getattr(job, "employment_type", None) or ""
    if employment_type in _DISQUALIFYING_EMPLOYMENT_TYPES:
        _fail(
            "employment_type",
            f"Employment type {employment_type} is not co-op/internship.",
            employment_type,
        )

    # (2b) Clearly-professional role TITLE with NO student signal anywhere.
    # Covers the common full-time posting whose title omits "full-time" (e.g.
    # "Operations Manager", "Lead Technician") and would otherwise sit in the
    # UNKNOWN bucket, relying entirely on the quota-limited AI stage to drop it.
    # Narrow by design: a genuinely ambiguous title (no professional-role noun)
    # does NOT match and still passes to AI, honoring the leniency rule.
    title = getattr(job, "title", None) or ""
    description = getattr(job, "description", None) or ""
    if (
        employment_type not in _DISQUALIFYING_EMPLOYMENT_TYPES
        and _PROFESSIONAL_TITLE_RE.search(title)
        and not _STUDENT_SIGNAL_RE.search(f"{title}\n{description}")
    ):
        match = _PROFESSIONAL_TITLE_RE.search(title)
        _fail(
            "professional_role",
            "Title is a professional (non-student) role with no co-op/intern "
            "signal.",
            match.group(0) if match else None,
        )

    # (3)/(4) ITAR + citizenship/sponsorship: read the persisted status (never
    # re-derived here). ITAR is a hard barrier; an explicit US-citizen / US-
    # person / PR / clearance requirement on a US-or-unknown-country role
    # resolves (in work_auth) to a hard status a Canadian citizen cannot meet.
    eligibility_status = getattr(job, "eligibility_status", None)
    citizenship_requirement = getattr(job, "citizenship_requirement", None) or ""
    if eligibility_status == INELIGIBLE_ITAR:
        _fail(
            "itar",
            "ITAR / US-person / US-citizen-only / security-clearance role.",
            citizenship_requirement or None,
        )
    elif eligibility_status == LIKELY_INELIGIBLE_SPONSORSHIP_REQUIRED:
        _fail(
            "citizenship",
            "Requires citizenship/PR/clearance the Canadian-citizen profile "
            "cannot satisfy, or sponsorship is unavailable.",
            citizenship_requirement or None,
        )

    # (5) advanced / completed-degree prerequisite.
    if degree_exp.requires_advanced_or_completed_degree:
        reason = (
            degree_exp.reasons[0]
            if degree_exp.reasons
            else "Requires an advanced or completed degree."
        )
        _fail(
            "degree",
            reason,
            degree_exp.evidence[0] if degree_exp.evidence else None,
        )

    # (6) senior / N+ years experience prerequisite.
    if degree_exp.requires_senior_experience:
        if degree_exp.min_years_experience is not None:
            reason = (
                f"Requires {degree_exp.min_years_experience}+ years of experience."
            )
        else:
            reason = "Requires senior-level experience."
        evidence = next(
            (e for e in degree_exp.evidence if e), None
        )
        _fail("experience", reason, evidence)

    # (7) term explicitly stated and incompatible. UNKNOWN never fails.
    term_season = getattr(job, "term_season", None) or TermSeason.UNKNOWN.value
    available = _norm(profile.available_terms)
    if term_season != TermSeason.UNKNOWN.value and available and (
        term_season.lower() not in available
    ):
        _fail(
            "term",
            f"Term {term_season} is incompatible with availability "
            f"({', '.join(sorted(available))}).",
            term_season,
        )

    # (8) duration explicitly stated and outside the profile range. UNKNOWN passes.
    duration = getattr(job, "duration_months", None)
    if duration is not None:
        minimum = profile.minimum_duration_months
        maximum = profile.maximum_duration_months
        if minimum is not None and duration < minimum:
            _fail(
                "duration",
                f"Duration {duration}mo is below the minimum {minimum}mo.",
                f"{duration}-month",
            )
        elif maximum is not None and duration > maximum:
            _fail(
                "duration",
                f"Duration {duration}mo is above the maximum {maximum}mo.",
                f"{duration}-month",
            )

    if verdict.eligible:
        verdict.reasons.append(
            "Passed deterministic hard filter (co-op/internship, no "
            "disqualifying requirement)."
        )
    return verdict
