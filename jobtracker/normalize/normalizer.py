"""Normalize a :class:`RawJob` into a :class:`CanonicalJob`.

Deterministic, rule-based: cleans HTML, splits location into
city/province_state/country, infers remote_status and employment_type from
keyword signals (defaulting to ``unknown``), and splits
requirements/preferred_qualifications/skills with section heuristics plus the
SPEC skill-signal list. Computes the stable ``description_hash``.
"""

from __future__ import annotations

import re

from jobtracker.connectors.base import RawJob
from jobtracker.normalize.hashing import description_hash
from jobtracker.normalize.html_clean import clean_html
from jobtracker.schemas.company import CompanyConfig
from jobtracker.schemas.job import (
    CanonicalJob,
    EmploymentType,
    RemoteStatus,
)

# SPEC "Skill signals" list (used for skill extraction).
SKILL_SIGNALS: tuple[str, ...] = (
    "CAD",
    "SolidWorks",
    "CATIA",
    "NX",
    "Creo",
    "Fusion 360",
    "GD&T",
    "FEA",
    "CFD",
    "ANSYS",
    "Abaqus",
    "MATLAB",
    "Simulink",
    "DFM",
    "DFA",
    "tolerance analysis",
    "prototyping",
    "3D printing",
    "CNC",
    "root cause analysis",
    "test fixtures",
    "BOM",
    "PLM",
    "thermal analysis",
    "fluid mechanics",
    "heat transfer",
    "kinematics",
    "dynamics",
)

# Canada province/territory tokens (full + abbreviations).
_CANADA_PROVINCES = {
    "ontario": "ON", "on": "ON",
    "quebec": "QC", "qc": "QC", "québec": "QC",
    "british columbia": "BC", "bc": "BC",
    "alberta": "AB", "ab": "AB",
    "manitoba": "MB", "mb": "MB",
    "saskatchewan": "SK", "sk": "SK",
    "nova scotia": "NS", "ns": "NS",
    "new brunswick": "NB", "nb": "NB",
    "newfoundland and labrador": "NL", "nl": "NL",
    "prince edward island": "PE", "pe": "PE",
    "northwest territories": "NT", "nt": "NT",
    "yukon": "YT", "yt": "YT",
    "nunavut": "NU", "nu": "NU",
}
_CANADA_TOKENS = {"canada", "ca"}

# A representative set of US state abbreviations + a few full names.
_US_STATES = {
    "al", "ak", "az", "ar", "ca", "co", "ct", "de", "fl", "ga", "hi", "id",
    "il", "in", "ia", "ks", "ky", "la", "me", "md", "ma", "mi", "mn", "ms",
    "mo", "mt", "ne", "nv", "nh", "nj", "nm", "ny", "nc", "nd", "oh", "ok",
    "or", "pa", "ri", "sc", "sd", "tn", "tx", "ut", "vt", "va", "wa", "wv",
    "wi", "wy",
}
_US_TOKENS = {"united states", "usa", "us", "u.s.", "u.s.a."}

_REMOTE_RE = re.compile(r"\bremote\b", re.IGNORECASE)
_HYBRID_RE = re.compile(r"\bhybrid\b", re.IGNORECASE)
_ONSITE_RE = re.compile(r"\b(on-?site|in-?office|in-?person)\b", re.IGNORECASE)

_COOP_RE = re.compile(r"\bco-?op\b", re.IGNORECASE)
_INTERN_RE = re.compile(r"\bintern(ship)?\b", re.IGNORECASE)
_NEWGRAD_RE = re.compile(r"\bnew\s*grad(uate)?\b", re.IGNORECASE)
_FULLTIME_RE = re.compile(r"\bfull[-\s]?time\b", re.IGNORECASE)

_SECTION_HEADINGS = {
    "requirements": "requirements",
    "qualifications": "requirements",
    "minimum qualifications": "requirements",
    "basic qualifications": "requirements",
    "what you'll need": "requirements",
    "what you will need": "requirements",
    "preferred qualifications": "preferred",
    "preferred": "preferred",
    "nice to have": "preferred",
    "bonus points": "preferred",
    "preferred skills": "preferred",
}

_BULLET_RE = re.compile(r"^[\-\*\u2022\u2023\u25E6\u2043\u2219]\s*")


class QuarantineError(ValueError):
    """Raised when a job fails the validation gate (missing required URLs)."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


def split_location(location: str) -> tuple[str | None, str | None, str | None]:
    """Split a free-form location into (city, province_state, country)."""
    if not location:
        return None, None, None
    tokens = [part.strip() for part in location.split(",") if part.strip()]
    if not tokens:
        return None, None, None

    city: str | None = None
    province_state: str | None = None
    country: str | None = None

    lowered = [tok.lower() for tok in tokens]

    # Country detection from any token.
    for raw, low in zip(tokens, lowered, strict=True):
        if low in _CANADA_TOKENS or raw == "CA":
            country = "Canada"
        elif low in _US_TOKENS:
            country = "United States"

    # Province/state detection.
    for raw, low in zip(tokens, lowered, strict=True):
        if low in _CANADA_PROVINCES:
            province_state = _CANADA_PROVINCES[low]
            if country is None:
                country = "Canada"
        elif low in _US_STATES and raw.upper() == raw:
            province_state = raw.upper()
            if country is None:
                country = "United States"

    # City is the first token that is not a province/state/country marker.
    markers = _CANADA_TOKENS | _US_TOKENS | set(_CANADA_PROVINCES) | _US_STATES
    for raw, low in zip(tokens, lowered, strict=True):
        if low not in markers:
            city = raw
            break

    return city, province_state, country


def infer_remote_status(title: str, description: str) -> RemoteStatus:
    """Infer remote/hybrid/onsite from title+description, default unknown."""
    text = f"{title}\n{description}"
    if _HYBRID_RE.search(text):
        return RemoteStatus.HYBRID
    if _REMOTE_RE.search(text):
        return RemoteStatus.REMOTE
    if _ONSITE_RE.search(text):
        return RemoteStatus.ONSITE
    return RemoteStatus.UNKNOWN


def infer_employment_type(title: str, description: str, hint: str | None) -> EmploymentType:
    """Infer employment type from title/description/hint, default unknown."""
    text = " ".join(filter(None, [title, hint or "", description]))
    if _COOP_RE.search(text):
        return EmploymentType.CO_OP
    if _INTERN_RE.search(text):
        return EmploymentType.INTERNSHIP
    if _NEWGRAD_RE.search(text):
        return EmploymentType.NEW_GRAD
    if _FULLTIME_RE.search(text):
        return EmploymentType.FULL_TIME
    return EmploymentType.UNKNOWN


def _heading_bucket(line: str) -> str | None:
    key = line.strip().rstrip(":").lower()
    return _SECTION_HEADINGS.get(key)


def split_sections(description: str) -> tuple[list[str], list[str]]:
    """Split requirements and preferred qualifications via section headings."""
    requirements: list[str] = []
    preferred: list[str] = []
    current: str | None = None

    for line in description.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        bucket = _heading_bucket(stripped)
        if bucket is not None:
            current = bucket
            continue
        bullet = _BULLET_RE.match(stripped)
        if bullet:
            item = _BULLET_RE.sub("", stripped).strip()
            if not item:
                continue
            if current == "preferred":
                preferred.append(item)
            elif current == "requirements":
                requirements.append(item)
        else:
            # A non-bullet line ends the current bullet section.
            current = None if bucket is None else current

    return requirements, preferred


def extract_skills(description: str) -> list[str]:
    """Return SPEC skill signals present in the description (original casing)."""
    found: list[str] = []
    for skill in SKILL_SIGNALS:
        pattern = re.compile(re.escape(skill), re.IGNORECASE)
        if pattern.search(description):
            found.append(skill)
    return found


def normalize(raw: RawJob, company: CompanyConfig) -> CanonicalJob:
    """Convert a :class:`RawJob` into a validated :class:`CanonicalJob`.

    Raises :class:`QuarantineError` when ``application_url`` or ``source_url``
    is missing so the caller can route the payload to quarantine instead of
    dropping it.
    """
    if not raw.application_url or not raw.source_url:
        missing = []
        if not raw.application_url:
            missing.append("application_url")
        if not raw.source_url:
            missing.append("source_url")
        raise QuarantineError(f"missing {', '.join(missing)}")

    description = clean_html(raw.description_html)
    city, province_state, country = split_location(raw.location)
    remote_status = infer_remote_status(raw.title, description)
    employment_type = infer_employment_type(
        raw.title, description, raw.employment_type_hint
    )
    requirements, preferred = split_sections(description)
    skills = extract_skills(description)

    hash_value = description_hash(description, raw.title, raw.location, requirements)

    return CanonicalJob(
        source_job_id=raw.source_job_id,
        company_id=company.id,
        company_name=company.name,
        title=raw.title,
        normalized_title=raw.title.strip().lower(),
        location=raw.location,
        city=city,
        province_state=province_state,
        country=country,
        remote_status=remote_status,
        employment_type=employment_type,
        department=raw.department,
        team=raw.team,
        description=description,
        requirements=requirements,
        preferred_qualifications=preferred,
        skills=skills,
        description_hash=hash_value,
        application_url=raw.application_url,
        source_url=raw.source_url,
        source_type=company.ats,
        posted_at=raw.posted_at,
        updated_at=raw.updated_at,
        raw_payload=raw.extra or None,
    )
