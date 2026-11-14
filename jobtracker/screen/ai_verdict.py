"""Pure Stage-2 AI eligibility verdict (FEAT-003).

Maps a parsed SPEC AI analysis (the ``result`` dict produced by
:mod:`jobtracker.services.ai_service`) to a single eligible / not-eligible
boolean, so the dashboard can intersect the Stage-1 survivors with the AI
judgment without any network access at read time.

The mapping is deliberately conservative and NON-blocking on uncertainty:
missing or ``unknown`` assessments never make a job ineligible -- only an
explicit ``skip`` recommendation or an assessment that plainly states the
candidate does not qualify removes a survivor. Everything else (``apply`` /
``maybe`` with clean or unknown assessments) stays eligible.
"""

from __future__ import annotations

#: Assessment fields whose free-text value can declare the candidate ineligible.
#: These mirror the hard-filter dimensions (work authorization, duration, term).
_BLOCKING_ASSESSMENT_FIELDS: tuple[str, ...] = (
    "work_authorization_assessment",
    "duration_assessment",
    "term_assessment",
)

#: Case-insensitive substrings that, when present in a blocking assessment,
#: indicate the role explicitly does NOT fit the candidate. Kept as a documented
#: module constant so the mapping is auditable and easy to extend.
NEGATIVE_CUES: tuple[str, ...] = (
    "not eligible",
    "ineligible",
    "cannot",
    "does not qualify",
    "doesn't qualify",
    "do not qualify",
    "not qualify",
    "requires us citizen",
    "requires u.s. citizen",
    "us citizen only",
    "u.s. citizen only",
    "citizens only",
    "incompatible",
    "too long",
    "too short",
    "not available",
    "not a fit",
    "no sponsorship",
    "not offered",
)


def _assessment_is_negative(text: object) -> bool:
    """Return True when a free-text assessment explicitly states ineligibility.

    Missing / empty / non-string values are treated as NON-blocking (unknown),
    so uncertainty never removes a job.
    """
    if not isinstance(text, str):
        return False
    lowered = text.strip().lower()
    if not lowered:
        return False
    return any(cue in lowered for cue in NEGATIVE_CUES)


def ai_eligibility(result: dict) -> bool:
    """Map a parsed SPEC AI analysis to an eligible (True) / not (False) verdict.

    Mapping (see :data:`NEGATIVE_CUES`):

    * ``recommendation == "skip"`` -> NOT eligible (False).
    * Any of ``work_authorization_assessment`` / ``duration_assessment`` /
      ``term_assessment`` containing a negative cue (case-insensitive) -> NOT
      eligible (False), even when the recommendation is ``apply`` / ``maybe``.
    * ``recommendation in {"apply", "maybe"}`` with no explicit not-eligible
      assessment -> eligible (True).
    * Missing / empty / ``unknown`` assessments are NON-blocking and never make
      a job ineligible on their own.

    An empty or missing ``result`` is treated as eligible (True): the caller
    uses "no verdict" as the never-empty Stage-1 fallback, so a blank analysis
    must not silently hide a survivor.
    """
    if not result:
        return True

    verify = result.get("_verify")
    if isinstance(verify, dict) and verify.get("eligible") is False:
        return False  # the second-opinion model found a disqualifier
    failed_rules = result.get("failed_rules")
    if isinstance(failed_rules, list) and any(
        str(rule).strip() for rule in failed_rules
    ):
        return False
    explicit = result.get("eligible")
    if explicit is False or str(explicit).strip().lower() == "false":
        return False
    recommendation = str(result.get("recommendation", "")).strip().lower()
    if recommendation == "skip":
        return False
    # Under the current rules the model gives a definite answer. A "maybe"
    # WITH an explicit eligible flag is decided by that flag; a bare "maybe"
    # (older analyses) keeps the previous behaviour below.
    if recommendation == "maybe" and explicit is True:
        return True

    for field in _BLOCKING_ASSESSMENT_FIELDS:
        if _assessment_is_negative(result.get(field)):
            return False

    return True
