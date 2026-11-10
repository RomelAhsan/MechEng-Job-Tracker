"""Deterministic degree-level & experience-requirement extraction (Stage 1).

Pure functions, no network, no guessing. Mirrors the evidence-carrying style of
:mod:`jobtracker.extract.term` and :mod:`jobtracker.extract.work_auth`.

Given a job's title + description the extractor detects HARD prerequisites a
first-year undergraduate cannot meet:

* An advanced or already-completed degree requirement (master's / PhD / MBA /
  "must have completed" / "Bachelor's degree required").
* A senior-experience prerequisite (senior/staff/principal/lead seniority
  framing, "experienced professional", or an "N+ years of experience" clause).

Every positive detection appends a human-readable reason AND the exact matched
substring to ``evidence``. Nothing is guessed: when no pattern matches the
result is all-False / empty so the ambiguous posting survives to the AI stage.
Patterns are conservative: a missed ambiguous phrase (survives Stage 2) is
preferred over a false hard-exclude.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

# --- Advanced / completed-degree prerequisites -----------------------------
# A master's / PhD / MBA requirement, or an explicitly completed degree, that a
# first-year undergraduate cannot satisfy.
_ADVANCED_DEGREE_RE = re.compile(
    r"\b(?:master'?s?|m\.?s\.?(?:c\.?)?|m\.?eng\.?|"
    r"ph\.?d\.?|doctorate|doctoral|mba)\b"
    r"[^.?!\n]{0,40}?\b(?:degree\s+)?(?:required|require[sd]|"
    r"must\s+have|or\s+equivalent|mandatory)\b"
    r"|\b(?:required|must\s+have|minimum\s+of)\b[^.?!\n]{0,40}?"
    r"\b(?:master'?s?|m\.?s\.?(?:c\.?)?|m\.?eng\.?|"
    r"ph\.?d\.?|doctorate|doctoral|mba)\b",
    re.IGNORECASE,
)

# A standalone advanced-degree noun that is itself a requirement phrasing
# ("PhD required", "MBA required") -- covered above -- plus bare advanced
# degrees that are only requirements when paired with required-framing. We also
# treat an MBA/PhD mentioned as "must hold a ..." via the generic framing arm.

# "Must have completed" / "completed degree" / "graduated" style prerequisites.
_COMPLETED_DEGREE_RE = re.compile(
    r"\bmust\s+have\s+(?:completed|graduated|a\s+completed)\b"
    r"|\bcompleted\s+(?:bachelor'?s?|degree|undergraduate\s+degree)\b"
    r"|\b(?:already\s+)?graduated\b"
    r"|\bdegree\s+(?:must\s+be\s+)?completed\b",
    re.IGNORECASE,
)

# A completed bachelor's requirement, phrased as a prerequisite the first-year
# cannot yet meet. Explicitly guards against "working toward / pursuing /
# currently enrolled / in progress" (those are student-friendly and must NOT
# match).
_BACHELOR_REQUIRED_RE = re.compile(
    r"\bbachelor'?s?(?:\s+degree)?\s+(?:is\s+)?required\b"
    r"|\bmust\s+have\s+(?:a\s+|an\s+|completed\s+)?(?:a\s+)?bachelor'?s?\b"
    r"|\bcompleted\s+bachelor'?s?\b"
    r"|\brequires?\s+(?:a\s+)?bachelor'?s?(?:\s+degree)?\b",
    re.IGNORECASE,
)

# Student-friendly phrasings that negate a bachelor's "requirement" (the
# candidate is still working toward the degree).
_ENROLLED_RE = re.compile(
    r"\bworking\s+toward(?:s)?\b"
    r"|\bcurrently\s+(?:enrolled|pursuing|studying)\b"
    r"|\bpursuing\s+(?:a\s+)?(?:bachelor'?s?|degree|undergraduate)\b"
    r"|\benrolled\s+in\b"
    r"|\bin\s+progress\b"
    r"|\bexpected\s+(?:graduation|to\s+graduate)\b",
    re.IGNORECASE,
)

# --- Senior-experience prerequisites ---------------------------------------
# A seniority TITLE ("Senior Mechanical Engineer", "Principal Software
# Engineer", "Staff Research Scientist"). The seniority prerequisite is a
# property of the ROLE BEING HIRED, which lives in the TITLE -- so this pattern
# is matched against the title ONLY. Allowing 0-2 qualifier tokens between the
# seniority word and the role noun catches the common MechE title shape, but if
# it were run over the description it would also match prose about senior
# COLLEAGUES an intern reports to ("report to a senior design engineer"), which
# is boilerplate, not a prerequisite on the candidate. (Guards against "lead a
# project" / "team lead opportunities" by requiring the role-noun shape.)
_SENIORITY_TITLE_RE = re.compile(
    r"\b(?:senior|staff|principal)\s+(?:[a-z]+\s+){0,2}?"
    r"(?:engineer|developer|scientist|designer|analyst|"
    r"manager|architect|specialist|role|position|level)\b"
    r"|\b(?:senior|staff|principal|lead)[-\s]level\b"
    r"|\blead\s+engineer\b",
    re.IGNORECASE,
)

# Seniority prerequisites genuinely framed as a requirement on the CANDIDATE,
# which may appear anywhere in the title OR description. "Senior-level
# candidate" / "experienced professional" describe who the posting wants, not a
# coworker, so they are safe to search over the combined blob.
_SENIORITY_DESC_RE = re.compile(
    r"\b(?:senior|staff|principal|lead)[-\s]level\b"
    r"|\bexperienced\s+professional\b",
    re.IGNORECASE,
)

# "N years of experience" / "N+ years" / "minimum of N years" capturing the int.
_YEARS_RE = re.compile(
    r"\b(\d{1,2})\s*\+?\s*years?(?:\s+of)?\s+(?:relevant\s+|professional\s+|"
    r"industry\s+|work\s+)?experience\b"
    r"|\bminimum\s+of\s+(\d{1,2})\s*\+?\s*years?\b"
    r"|\bat\s+least\s+(\d{1,2})\s*\+?\s*years?\b",
    re.IGNORECASE,
)

# A years-of-experience clause framed as merely PREFERRED / nice-to-have rather
# than required. Mirrors the degree rule (fix: "preferred" is ambiguous, not a
# hard exclude): such a line survives Stage 1 and is routed to the AI stage.
# Checked only against the text immediately AFTER the matched years clause so an
# "N years ... required" clause elsewhere still hard-fails.
_YEARS_PREFERRED_RE = re.compile(
    r"^[^.?!\n]{0,20}?\b(?:preferred|nice\s+to\s+have|"
    r"a\s+plus|is\s+a\s+bonus|desirable)\b",
    re.IGNORECASE,
)


@dataclass
class DegreeExperienceExtraction:
    """Result of degree-level/experience extraction (evidence-carrying)."""

    requires_advanced_or_completed_degree: bool = False
    requires_senior_experience: bool = False
    min_years_experience: int | None = None
    reasons: list[str] = field(default_factory=list)
    evidence: list[str] = field(default_factory=list)


def _match_years(text: str) -> tuple[int, str] | None:
    """Return (years, evidence) from a *required* N-years-of-experience clause.

    A clause framed as merely preferred / nice-to-have ("2 years of experience
    preferred") is ambiguous for a first-year and is NOT treated as a hard
    requirement -- it survives to the AI stage, mirroring the degree rule. The
    explicit "minimum of" / "at least" framings are always requirements.
    """
    match = _YEARS_RE.search(text)
    if match is None:
        return None
    captured = next((g for g in match.groups() if g), None)
    if captured is None:
        return None
    years = int(captured)
    if years <= 0 or years > 50:
        return None
    # The first alternation (group 1) is the bare "N years of experience" arm,
    # which may be softened by a following "preferred" / "a plus". The
    # "minimum of" / "at least" arms are inherently requirement framing.
    if match.group(1) is not None and _YEARS_PREFERRED_RE.search(
        text[match.end() :]
    ):
        return None
    return years, match.group(0).strip()


def extract_degree_experience(
    title: str, description: str = ""
) -> DegreeExperienceExtraction:
    """Extract advanced/completed-degree and senior-experience requirements.

    Returns all-False / empty when nothing matches (never guesses), so an
    ambiguous posting survives to the later AI stage.
    """
    text = f"{title}\n{description}".strip()
    result = DegreeExperienceExtraction()

    enrolled = _ENROLLED_RE.search(text)

    advanced = _ADVANCED_DEGREE_RE.search(text)
    completed = _COMPLETED_DEGREE_RE.search(text)
    # A bachelor's "requirement" only counts when NOT phrased as in-progress
    # study (working toward / pursuing / currently enrolled / in progress).
    bachelor = None if enrolled is not None else _BACHELOR_REQUIRED_RE.search(text)

    if advanced is not None:
        result.requires_advanced_or_completed_degree = True
        quote = advanced.group(0).strip()
        result.reasons.append(f"Requires an advanced degree: {quote}")
        result.evidence.append(quote)
    if completed is not None:
        result.requires_advanced_or_completed_degree = True
        quote = completed.group(0).strip()
        result.reasons.append(f"Requires a completed degree: {quote}")
        result.evidence.append(quote)
    if bachelor is not None:
        result.requires_advanced_or_completed_degree = True
        quote = bachelor.group(0).strip()
        result.reasons.append(f"Requires a completed Bachelor's degree: {quote}")
        result.evidence.append(quote)

    years_hit = _match_years(text)
    if years_hit is not None:
        years, quote = years_hit
        result.min_years_experience = years
        result.reasons.append(f"Requires {years}+ years of experience: {quote}")
        result.evidence.append(quote)

    # Seniority as a property of the role being hired lives in the TITLE; a
    # senior COLLEAGUE mentioned in the description (common intern boilerplate)
    # must NOT hard-exclude. Requirement-framed seniority on the candidate
    # ("senior-level candidate", "experienced professional") is safe to detect
    # anywhere in the combined text.
    seniority = _SENIORITY_TITLE_RE.search(title) or _SENIORITY_DESC_RE.search(text)
    if seniority is not None:
        quote = seniority.group(0).strip()
        result.reasons.append(f"Senior-level role: {quote}")
        result.evidence.append(quote)

    if seniority is not None or (
        result.min_years_experience is not None and result.min_years_experience >= 1
    ):
        result.requires_senior_experience = True

    return result
