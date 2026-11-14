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
from jobtracker.extract.term import normalize_profile_term, term_set
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
# The narrow, explicit exception that lets a US role at an ITAR-restricted
# company survive: the posting clearly offers visa sponsorship or welcomes
# non-US / international / Canadian applicants. Absent this, an ITAR-company US
# role is hard-excluded.
_EXPLICIT_OPEN_RE = re.compile(
    r"visa\s+sponsorship\s+(?:is\s+)?(?:available|offered|provided)"
    r"|will\s+sponsor"
    r"|sponsorship\s+available"
    r"|open\s+to\s+(?:international|non[-\s]?u\.?s\.?|canadian)"
    r"|international\s+(?:applicants|candidates)\s+welcome"
    r"|no\s+citizenship\s+requirement",
    re.IGNORECASE,
)

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
    #: True when this is a non-Canada role at an ITAR-restricted company that is
    #: NOT explicitly barred by posting text -- i.e. ambiguous and routed to the
    #: AI stage for a citizenship-aware decision (not hard-excluded here).
    itar_company_ambiguous: bool = False
    #: Which screening level decided an eligible job:
    #:   "auto" -- level 1: a clearly student-titled (intern/co-op) Canadian
    #:             role with no disqualifier. Shown without needing the AI.
    #:   "ai"   -- passed every hard rule but is ambiguous (US/unknown country,
    #:             or the student signal is only in the description, or the
    #:             wording is unclear). Shown only once the AI confirms it.
    #: ``None`` when the job failed a hard rule.
    tier: str | None = None


# Level-1 signal: the TITLE says this is a student role.
_TITLE_STUDENT_RE = re.compile(
    r"\b(?:intern(?:s|ship|ships)?|co-?ops?|coop|student|work\s+term|"
    r"placement|apprentice(?:ship)?|stagiaire)\b",
    re.IGNORECASE,
)
# French "Stage" = internship ("Stage - Tunnels", "Stage en génie mécanique").
# Only a leading "Stage" followed by a separator / French preposition counts,
# so "Multi-Stage Compressor" or "Stage 2 Engineer" do not.
_TITLE_STAGE_RE = re.compile(
    r"^\s*stages?\s*(?:[-–—:|]|en\b|de\b|d['’]|\(|20\d\d)", re.IGNORECASE
)
# Weaker signal found only in the DESCRIPTION: plausibly a student role, so the
# AI decides. ("students" or "university" alone is common in full-time
# boilerplate, so stronger phrasings are required.)
_DESC_STUDENT_RE = re.compile(
    r"\b(?:currently\s+(?:enrolled|pursuing|studying)|enrolled\s+in\s+(?:a|an)\s+"
    r"(?:undergraduate|bachelor|engineering|university|college)|pursuing\s+(?:a|an|your)\s+"
    r"(?:bachelor|undergraduate|degree|engineering)|co-?op\s+(?:term|student|program|"
    r"placement|position)|work\s+term|internship\s+(?:term|program|position|opportunity)|"
    r"this\s+(?:internship|co-?op)|summer\s+student|student\s+(?:position|role|"
    r"opportunity|program)|returning\s+to\s+(?:school|university|studies))\b",
    re.IGNORECASE,
)
_NORTH_AMERICA = {"canada", "united states"}

# Graduation-window requirements ("Expected graduation between Fall 2027 and
# Summer 2028", "graduating in 2027 or 2028"). A sentence that only sets a
# LOWER bound ("graduating 2027 or later", "at least one term remaining
# after") is not a window and is ignored.
_GRAD_SENTENCE_RE = re.compile(r"[^.!?\n]*\bgraduat[^.!?\n]*", re.IGNORECASE)
_GRAD_OPEN_ENDED_RE = re.compile(
    r"\b(?:or\s+later|or\s+after|and\s+(?:later|beyond)|onwards?|beyond|after|"
    r"at\s+least|remaining|return(?:ing)?\s+to|no\s+earlier\s+than|or\s+beyond)\b",
    re.IGNORECASE,
)
_GRAD_YEAR_RE = re.compile(r"\b(20[2-4]\d)\b")


def graduation_window_conflict(text: str, grad_year: int | None) -> str | None:
    """Return the sentence when the posting requires graduating before ``grad_year``."""
    if not grad_year or not text:
        return None
    for match in _GRAD_SENTENCE_RE.finditer(text):
        sentence = match.group(0).strip()
        if len(sentence) > 300 or _GRAD_OPEN_ENDED_RE.search(sentence):
            continue
        years = [int(y) for y in _GRAD_YEAR_RE.findall(sentence)]
        if years and max(years) < grad_year:
            return sentence
    return None


def _norm(values: list[str] | None) -> set[str]:
    return {v.strip().lower() for v in (values or []) if v and v.strip()}


def compute_stage1_eligibility(
    job_or_canonical: object,
    profile: UserProfile,
    degree_exp: DegreeExperienceExtraction,
    *,
    itar_restricted_company: bool = False,
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

    # (2a) Must be a student role at all. A posting with NO intern/co-op/
    # student signal in the title AND none of the strong student phrasings in
    # the description is a regular (full-time) job -- removed immediately.
    title_student = (
        _TITLE_STUDENT_RE.search(title) is not None
        or _TITLE_STAGE_RE.search(title) is not None
    )
    desc_student = _DESC_STUDENT_RE.search(description) is not None
    if (
        employment_type not in _DISQUALIFYING_EMPLOYMENT_TYPES
        and not title_student
        and not desc_student
    ):
        _fail(
            "not_student_role",
            "No co-op / internship / student signal: this is a regular job.",
            title or None,
        )

    # (2d) Located outside Canada and the US: needs that country's work
    # authorization, which the Canadian-citizen profile does not have.
    country_name = (getattr(job, "country", None) or "").strip()
    if country_name and country_name.lower() not in _NORTH_AMERICA:
        _fail(
            "foreign_country",
            f"Located in {country_name}; requires local work authorization.",
            getattr(job, "location", None) or country_name,
        )

    # (2c) ITAR-restricted COMPANY policy. US aerospace/defense employers
    # (SpaceX, Lockheed, Northrop, ...) require US citizenship (ITAR/EAR) on
    # essentially every US-located role. The earlier "route the ambiguous ones
    # to AI" approach let hundreds of plainly-ineligible US SpaceX roles linger
    # until the quota-limited AI reached them -- the user is categorically
    # ineligible for all of them, so we HARD-EXCLUDE a non-Canada role at an
    # ITAR company at Stage 1. The ONE honest exception: a posting that
    # EXPLICITLY offers visa sponsorship or welcomes non-US persons survives
    # (rare), so a genuinely-open role is not wrongly hidden. Canada-located
    # roles are always unaffected.
    country = (getattr(job, "country", None) or "").strip().lower()
    _title = getattr(job, "title", None) or ""
    _desc = getattr(job, "description", None) or ""
    explicitly_open = _EXPLICIT_OPEN_RE.search(f"{_title}\n{_desc}") is not None
    if itar_restricted_company and country != "canada" and not explicitly_open:
        where = getattr(job, "location", None) or "outside Canada"
        _fail(
            "itar_company",
            "US aerospace/defense employer requiring US citizenship (ITAR/EAR); "
            "only Canada-located roles are eligible.",
            str(where),
        )
    # Still flag the (now rare) survivors so the AI double-checks them.
    verdict.itar_company_ambiguous = bool(
        itar_restricted_company and country != "canada" and explicitly_open
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

    # (4b) Graduation window that ends before the student graduates.
    grad_conflict = graduation_window_conflict(
        description, getattr(profile, "expected_graduation_year", None)
    )
    if grad_conflict:
        _fail(
            "graduation_window",
            "Requires graduating before your expected graduation year "
            f"({profile.expected_graduation_year}).",
            grad_conflict[:200],
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
    # A posting may name several terms ("Spring/Summer 2027" -> winter,spring);
    # it fails only when NONE of them is a term the student can work.
    term_season = getattr(job, "term_season", None) or TermSeason.UNKNOWN.value
    job_terms = term_set(term_season)
    available = {
        t for t in (normalize_profile_term(x) for x in profile.available_terms) if t
    }
    if job_terms and available and not (job_terms & available):
        _fail(
            "term",
            f"Term ({', '.join(sorted(job_terms))}) does not match your "
            f"availability ({', '.join(sorted(available))}).",
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
        is_canada = country_name.lower() == "canada"
        if title_student and is_canada and not verdict.itar_company_ambiguous:
            verdict.tier = "auto"
            verdict.reasons.append(
                "Level 1: intern/co-op role in Canada with no disqualifying "
                "requirement -- no AI check needed."
            )
        else:
            verdict.tier = "ai"
            why = []
            if not title_student:
                why.append("student signal only in the description")
            if not is_canada:
                why.append(
                    "located in the US" if country_name else "location not stated"
                )
            if verdict.itar_company_ambiguous:
                why.append("aerospace/defense employer")
            verdict.reasons.append(
                "Passed every hard rule; sent to the AI to confirm ("
                + ", ".join(why or ["ambiguous wording"]) + ")."
            )
    return verdict
