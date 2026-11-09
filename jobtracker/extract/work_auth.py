"""Citizenship & visa-sponsorship extraction (design 8.2).

Deterministic keyword/phrase matching producing ``citizenship_requirement``,
``work_authorization_requirement``, ``visa_sponsorship`` (provided /
not_provided / unknown), a ``sponsorship_confidence`` (0-1 by match strength),
the exact ``sponsorship_evidence`` quote, and the derived ``eligibility_status``
for the single Canadian-citizen profile per the SPEC rules.

SPEC eligibility rules (for a Canadian citizen):

* Canadian roles are eligible unless they explicitly require PR/citizenship of
  another country.
* U.S. roles are eligible only when sponsorship is explicitly provided or the
  role explicitly accepts Canadian citizens / work authorization.
* A U.S. role that states sponsorship is unavailable ->
  ``likely_ineligible_sponsorship_required``.
* Missing information -> ``possibly_eligible_unknown`` (never auto-ineligible).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from jobtracker.schemas.job import VisaSponsorship

# --- Derived eligibility status values -------------------------------------
ELIGIBLE = "eligible"
POSSIBLY_ELIGIBLE_UNKNOWN = "possibly_eligible_unknown"
LIKELY_INELIGIBLE_SPONSORSHIP_REQUIRED = "likely_ineligible_sponsorship_required"
#: Hard barrier for a Canadian citizen: ITAR / export-control / US-person /
#: US-citizen-only requirement. Jobs with this status are filtered out of the
#: dashboard ENTIRELY (never merely flagged).
INELIGIBLE_ITAR = "ineligible_itar"


def _quote(match: re.Match[str]) -> str:
    """Return the matched substring as a trimmed evidence quote."""
    return match.group(0).strip()


# Phrases stating sponsorship is NOT available (strong negative).
_NO_SPONSOR_RE = re.compile(
    r"(?:(?:does|will|can)\s*not|no|not|unable to|cannot|won't)\s+"
    r"(?:offer|provide|sponsor|able to sponsor|offering|providing)?\s*"
    r"(?:visa\s+)?sponsorship"
    r"|not\s+(?:offer|provide)\s+(?:visa\s+)?sponsorship"
    r"|sponsorship\s+is\s+not\s+(?:available|offered|provided)"
    r"|unable\s+to\s+(?:offer|provide)\s+(?:visa\s+)?sponsorship"
    r"|no\s+visa\s+sponsorship",
    re.IGNORECASE,
)

# Phrases stating sponsorship IS available (strong positive).
_SPONSOR_RE = re.compile(
    r"(?:visa\s+)?sponsorship\s+(?:is\s+)?(?:available|provided|offered)"
    r"|(?:we|employer)?\s*(?:will|can|do)\s+(?:offer|provide|sponsor)\s+"
    r"(?:visa\s+)?sponsorship"
    r"|will\s+sponsor\b"
    r"|sponsor(?:ship)?\s+(?:is\s+)?available",
    re.IGNORECASE,
)

# U.S. citizenship / PR requirement (strong citizenship constraint).
_US_CITIZEN_RE = re.compile(
    r"(?:must be|require[sd]?|only)\s+(?:a\s+)?u\.?s\.?\s*"
    r"(?:citizen|permanent resident)"
    r"|u\.?s\.?\s+citizen(?:ship)?\s+(?:is\s+)?required"
    r"|requires?\s+u\.?s\.?\s+citizenship"
    r"|u\.?s\.?\s+citizens?\s+only",
    re.IGNORECASE,
)

# Explicit acceptance of Canadian citizens / work authorization.
_CA_ACCEPTED_RE = re.compile(
    r"canadian\s+citizens?\s+(?:are\s+)?(?:welcome|accepted|eligible)"
    r"|open\s+to\s+canadian\s+(?:citizens|work authorization)"
    r"|canadian\s+work\s+authorization\s+(?:is\s+)?accepted",
    re.IGNORECASE,
)

# Foreign (non-Canadian) PR/citizenship requirement that blocks a Canadian.
_FOREIGN_PR_RE = re.compile(
    r"(?:must be|require[sd]?)\s+(?:a\s+)?u\.?s\.?\s*"
    r"(?:citizen|permanent resident)"
    r"|u\.?s\.?\s+citizens?\s+only",
    re.IGNORECASE,
)

# ITAR / export-control / US-person / US-citizen-only barrier. A match is a
# hard ineligibility for a Canadian citizen and takes precedence over the
# sponsorship-based eligibility derivation. Tolerates U.S./US/United States
# variants.
_US = r"(?:u\.?\s*s\.?|united states)"

# Explicit-barrier arms: these phrasings ARE the eligibility barrier on their
# own (a Canadian citizen cannot satisfy them), so they match unconditionally
# and always resolve to INELIGIBLE_ITAR. Kept exactly as reviewed.
_ITAR_BARRIER_RE = re.compile(
    rf"(?:must be|require[sd]?|are)\s+(?:a\s+)?{_US}\.?\s*persons?\b"
    rf"|(?:must be|require[sd]?|only)\s+(?:a\s+)?{_US}\s*"
    r"(?:citizen|permanent resident)"
    rf"|{_US}\s+citizen(?:ship)?\s+(?:is\s+)?required"
    rf"|requires?\s+{_US}\s+citizenship"
    rf"|{_US}\s+persons?\s+only"
    rf"|{_US}\s+citizens?\s+(?:or\s+permanent\s+residents?\s+)?only",
    re.IGNORECASE,
)

# Bare-keyword arms: ``itar``, ``export control``, ``\bEAR\b`` and a bare
# ``U.S. person`` mention name a TOPIC/SKILL and are NOT barriers on their own
# ("ITAR experience a plus", "export control training provided"). They only
# indicate an eligibility barrier when framed as a requirement, so they are
# gated behind nearby barrier-framing wording. Without that framing the role
# falls through to the normal possibly_eligible_unknown path (never hidden).
_ITAR_KEYWORD = (
    r"itar(?:[-\s]*compliance|[-\s]*compliant)?"
    r"|export[-\s]*control(?:led|s)?(?:\s+regulations?)?"
    r"|\bEAR\b"
    rf"|{_US}\.?\s*persons?\b"
)
# Barrier-framing cues that must appear next to a bare keyword for it to count
# as a genuine eligibility barrier rather than a topic/skill mention.
_BARRIER_CUE = (
    r"require[sd]?|must|only|eligib(?:le|ility)|due to|restricted to"
    r"|limited to|authorized to access|clearance required"
)
# Keyword followed by framing, OR framing followed by keyword, within a short
# window so unrelated sentences do not bridge a false match.
_ITAR_KEYWORD_RE = re.compile(
    rf"(?:{_ITAR_KEYWORD})[^.?!\n]{{0,60}}\b(?:{_BARRIER_CUE})\b"
    rf"|\b(?:{_BARRIER_CUE})\b[^.?!\n]{{0,60}}(?:{_ITAR_KEYWORD})",
    re.IGNORECASE,
)


def _match_itar(text: str) -> re.Match[str] | None:
    """Return an ITAR/US-citizen-only barrier match, or None.

    Explicit-barrier phrasings match on their own; bare topic/skill keywords
    only match when framed as a requirement so mere mentions stay visible.
    """
    return _ITAR_BARRIER_RE.search(text) or _ITAR_KEYWORD_RE.search(text)


@dataclass
class WorkAuthExtraction:
    """Result of work-authorization extraction (evidence-carrying)."""

    citizenship_requirement: str = ""
    work_authorization_requirement: str = ""
    visa_sponsorship: VisaSponsorship = VisaSponsorship.UNKNOWN
    sponsorship_confidence: float = 0.0
    sponsorship_evidence: str = ""
    eligibility_status: str = POSSIBLY_ELIGIBLE_UNKNOWN
    reasons: list[str] = field(default_factory=list)


def _is_us_role(country: str | None, text: str) -> bool:
    if country and country.strip().lower() in {"united states", "usa", "us"}:
        return True
    return False


def _is_ca_role(country: str | None) -> bool:
    return bool(country and country.strip().lower() == "canada")


def extract_work_auth(
    description: str,
    *,
    country: str | None = None,
) -> WorkAuthExtraction:
    """Extract citizenship/sponsorship signals and derive eligibility.

    ``country`` is the normalized job country (``"Canada"`` / ``"United
    States"`` / ``None``) used to apply the Canadian-citizen eligibility rules.
    """
    text = description or ""
    result = WorkAuthExtraction()

    no_sponsor = _NO_SPONSOR_RE.search(text)
    sponsor = _SPONSOR_RE.search(text)
    us_citizen = _US_CITIZEN_RE.search(text)
    ca_accepted = _CA_ACCEPTED_RE.search(text)
    foreign_pr = _FOREIGN_PR_RE.search(text)
    itar = _match_itar(text)

    # Sponsorship signal (negative wins over positive when both appear).
    if no_sponsor is not None:
        result.visa_sponsorship = VisaSponsorship.NOT_PROVIDED
        result.sponsorship_confidence = 0.9
        result.sponsorship_evidence = _quote(no_sponsor)
    elif sponsor is not None:
        result.visa_sponsorship = VisaSponsorship.PROVIDED
        result.sponsorship_confidence = 0.9
        result.sponsorship_evidence = _quote(sponsor)
    else:
        result.visa_sponsorship = VisaSponsorship.UNKNOWN
        result.sponsorship_confidence = 0.0
        result.sponsorship_evidence = ""

    # ITAR / export-control / US-citizen-only is a HARD barrier that takes
    # precedence over the sponsorship-based eligibility derivation below.
    if itar is not None:
        quote = _quote(itar)
        result.citizenship_requirement = quote
        result.work_authorization_requirement = quote
        if not result.sponsorship_evidence:
            result.sponsorship_evidence = quote
        result.eligibility_status = INELIGIBLE_ITAR
        result.reasons.append(f"ITAR/US-citizen-only requirement: {quote}")
        return result

    if us_citizen is not None:
        result.citizenship_requirement = _quote(us_citizen)
        result.work_authorization_requirement = _quote(us_citizen)
    elif ca_accepted is not None:
        result.citizenship_requirement = _quote(ca_accepted)

    _derive_eligibility(result, country, ca_accepted, foreign_pr)
    return result


def _derive_eligibility(
    result: WorkAuthExtraction,
    country: str | None,
    ca_accepted: re.Match[str] | None,
    foreign_pr: re.Match[str] | None,
) -> None:
    """Apply the SPEC Canadian-citizen eligibility decision rules."""
    if _is_ca_role(country):
        if foreign_pr is not None:
            result.eligibility_status = LIKELY_INELIGIBLE_SPONSORSHIP_REQUIRED
            result.reasons.append(
                "Canadian role requires foreign PR/citizenship: "
                f"{_quote(foreign_pr)}"
            )
        else:
            result.eligibility_status = ELIGIBLE
            result.reasons.append("Canadian role; Canadian citizen eligible.")
        return

    if _is_us_role(country, ""):
        if result.visa_sponsorship == VisaSponsorship.PROVIDED:
            result.eligibility_status = ELIGIBLE
            result.reasons.append("U.S. role with sponsorship explicitly provided.")
        elif ca_accepted is not None:
            result.eligibility_status = ELIGIBLE
            result.reasons.append("U.S. role explicitly accepts Canadian citizens.")
        elif result.visa_sponsorship == VisaSponsorship.NOT_PROVIDED:
            result.eligibility_status = LIKELY_INELIGIBLE_SPONSORSHIP_REQUIRED
            result.reasons.append("U.S. role states sponsorship is not available.")
        else:
            result.eligibility_status = POSSIBLY_ELIGIBLE_UNKNOWN
            result.reasons.append("U.S. role; sponsorship status unknown.")
        return

    # Country unknown: never auto-ineligible unless a hard requirement blocks.
    if foreign_pr is not None:
        result.eligibility_status = LIKELY_INELIGIBLE_SPONSORSHIP_REQUIRED
        result.reasons.append("Role requires foreign PR/citizenship.")
    elif result.visa_sponsorship == VisaSponsorship.NOT_PROVIDED:
        result.eligibility_status = POSSIBLY_ELIGIBLE_UNKNOWN
        result.reasons.append("Location unknown; sponsorship not provided.")
    else:
        result.eligibility_status = POSSIBLY_ELIGIBLE_UNKNOWN
        result.reasons.append("Insufficient location/authorization information.")
