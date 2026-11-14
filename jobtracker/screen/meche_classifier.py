"""Recall-first mechanical-engineering relevance classifier (design 9).

Pure, deterministic, evidence-first. Returns a ``classification`` plus the
reasons behind it and a confidence. The guiding SPEC rule: never silently hide
an ambiguous mechanical-engineering role -- adjacent roles become
``possible_match`` with a reason, and ``not_relevant`` is used only when BOTH
the title and the description clearly indicate an unrelated field.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from jobtracker.schemas.job import Classification
from jobtracker.screen.signals import (
    ADJACENT_ROLE_SIGNALS,
    NEGATIVE_FIELD_SIGNALS,
    SKILL_SIGNALS,
    STRONG_TITLE_SIGNALS,
)


@dataclass
class ClassificationResult:
    """Result of mechanical-relevance classification."""

    classification: Classification
    classification_reasons: list[str] = field(default_factory=list)
    classification_confidence: float = 0.0


def _compile(signals: tuple[str, ...]) -> list[tuple[str, re.Pattern[str]]]:
    return [(sig, re.compile(r"\b" + re.escape(sig) + r"\b", re.IGNORECASE)) for sig in signals]


_STRONG_RE = _compile(STRONG_TITLE_SIGNALS)
_ADJACENT_RE = _compile(ADJACENT_ROLE_SIGNALS)
_SKILL_RE = _compile(SKILL_SIGNALS)
_NEGATIVE_RE = _compile(NEGATIVE_FIELD_SIGNALS)
# "Pure software" negative signal needs both a software cue and no hardware cue.
_SOFTWARE_RE = re.compile(
    r"\b(software engineer(?:ing)?|software develop\w*|full[-\s]?stack|"
    r"front[-\s]?end|back[-\s]?end|web develop\w*)\b",
    re.IGNORECASE,
)
_HARDWARE_CUE_RE = re.compile(
    r"\b(mechanical|hardware|product design|mechatronic|robotic|manufactur\w*|"
    r"thermal|aerospace|automotive)\b",
    re.IGNORECASE,
)


# The TITLE names a field outside mechanical engineering. Descriptions of
# software/data/IT/business roles often mention "mechanical" teams or CAD in
# passing, so a field named in the title decides unless the title itself also
# carries a mechanical/hardware cue.
_TITLE_OTHER_FIELD_RE = re.compile(
    r"\b(?:software|logiciels?|d[ée]veloppeu(?:r|se|r\(se\))|full[-\s]?stack|"
    r"front[-\s]?end|back[-\s]?end|web|data|donn[ée]es|machine\s+learning|ML|AI|"
    r"IA|intelligence\s+artificielle|IT|TI|information\s+technology|cyber\w*|"
    r"network\w*|r[ée]seautique|cloud|devops|firmware|embedded|"
    r"electrical|[ée]lectrique|electronics?|[ée]lectronique|"
    r"human\s+resources?|ressources\s+humaines|HR|talent|recruit\w*|"
    r"finance|financial|financi[eè]re?|accounting|comptab\w*|tax|audit|"
    r"marketing|brand|marque|communications?|sales|ventes?|legal|law|droit|"
    r"juridiques?|contracts?|contrats?|immigration|histopatholog\w*|biolog\w*|"
    r"agronom\w*|health\s*(?:&|and)\s*safety|EHS|business\s+(?:operations|"
    r"analyst|efficiency)|administrat\w*)\b",
    re.IGNORECASE,
)
_TITLE_ME_CUE_RE = re.compile(
    r"\b(?:mechanical|m[ée]canique|hydrom[ée]canique|electromechanical|"
    r"[ée]lectrom[ée]caniques?|hardware|mat[ée]riel|mechatronics?|robotics?|"
    r"manufactur\w*|fabrication|manufacturi[eè]re?|thermal|aerospace|"
    r"a[ée]ronautique|aerostructures?|automotive|product\s+design|"
    r"industrial\s+design|design\s+industriel|structur\w*|stress|contraintes|"
    r"propulsion|hvac|tooling|outillages?|maintenance|reliability|fiabilit[ée]|"
    r"materials?|test(?:ing)?|process|quality|qualit[ée]|power\s+engineering|"
    r"mill|plant|instrumentation|controls?)\b",
    re.IGNORECASE,
)
# The role NOUN itself is another discipline ("Electrical Engineer Intern,
# Robotics", "Software Engineer Intern, Robotics"). A robotics/plant context
# does not rescue it; only a core mechanical cue in the title does.
_TITLE_OTHER_ROLE_RE = re.compile(
    r"\b(?:electrical\s+engineer(?:ing)?|software\s+(?:engineer(?:ing)?|"
    r"develop\w*)|firmware|embedded\s+software|data\s+scien\w*|"
    r"machine\s+learning|ML\s+engineer|neuro\w*|full[-\s]?stack|"
    r"computer\s+(?:science|engineering)|middleware)\b",
    re.IGNORECASE,
)
_TITLE_CORE_ME_RE = re.compile(
    r"\b(?:mechanical|m[ée]canique|electromechanical|[ée]lectrom[ée]caniques?|"
    r"mechatronics?|hardware|manufactur\w*|product\s+design)\b",
    re.IGNORECASE,
)
# French mechanical / engineering signals (Quebec employers).
_FRENCH_STRONG_RE = re.compile(
    r"\b(?:g[ée]nie\s+m[ée]canique|conception\s+m[ée]canique|"
    r"ing[ée]nierie\s+m[ée]canique|hydrom[ée]canique|m[ée]canique)\b",
    re.IGNORECASE,
)
_FRENCH_ADJACENT_RE = re.compile(
    r"\b(?:g[ée]nie|ing[ée]nierie|ing[ée]nieur(?:e)?|industrialisation|"
    r"outillages?|fabrication|structures?|contraintes)\b",
    re.IGNORECASE,
)


def _hits(patterns: list[tuple[str, re.Pattern[str]]], text: str) -> list[str]:
    return [sig for sig, pattern in patterns if pattern.search(text)]


def classify(title: str, description: str = "") -> ClassificationResult:
    """Classify a posting's mechanical-engineering relevance.

    Priority: strong-title signals -> ``strong_match``; adjacent roles or skill
    signals -> ``possible_match``; a clearly-unrelated field in BOTH title and
    description -> ``not_relevant``; otherwise ``weak_match``.
    """
    title = title or ""
    description = description or ""
    combined = f"{title}\n{description}"
    reasons: list[str] = []

    other_role = _TITLE_OTHER_ROLE_RE.search(title)
    if other_role and not _TITLE_CORE_ME_RE.search(title):
        reasons.append(f"Title is a non-mechanical role: {other_role.group(0)}")
        return ClassificationResult(Classification.NOT_RELEVANT, reasons, 0.85)

    other_field = _TITLE_OTHER_FIELD_RE.search(title)
    if other_field and not _TITLE_ME_CUE_RE.search(title):
        reasons.append(
            f"Title names a non-mechanical field: {other_field.group(0)}"
        )
        return ClassificationResult(Classification.NOT_RELEVANT, reasons, 0.85)

    if _FRENCH_STRONG_RE.search(title):
        reasons.append("Strong mechanical title signal (French).")
        return ClassificationResult(Classification.STRONG_MATCH, reasons, 0.9)

    strong_hits = _hits(_STRONG_RE, combined)
    if strong_hits:
        reasons.append(f"Strong mechanical title/description signal: {strong_hits[0]}")
        return ClassificationResult(Classification.STRONG_MATCH, reasons, 0.95)

    adjacent_hits = _hits(_ADJACENT_RE, combined)
    french = _FRENCH_ADJACENT_RE.search(title)
    if french:
        adjacent_hits.append(french.group(0))
    skill_hits = _hits(_SKILL_RE, combined)
    if adjacent_hits or skill_hits:
        if adjacent_hits:
            reasons.append(
                f"Adjacent engineering role surfaced for review: {adjacent_hits[0]}"
            )
        if skill_hits:
            reasons.append(
                "Mechanical skill signal(s): " + ", ".join(skill_hits[:5])
            )
        confidence = 0.6 if adjacent_hits else 0.5
        return ClassificationResult(Classification.POSSIBLE_MATCH, reasons, confidence)

    # Clearly-unrelated only when BOTH title and description signal an
    # unrelated field (and there is no hardware/mechanical cue).
    title_negative = _hits(_NEGATIVE_RE, title)
    desc_negative = _hits(_NEGATIVE_RE, description)
    if title_negative and desc_negative and not _HARDWARE_CUE_RE.search(combined):
        reasons.append(
            "Title and description both indicate an unrelated field: "
            f"{title_negative[0]}"
        )
        return ClassificationResult(Classification.NOT_RELEVANT, reasons, 0.9)

    # Pure software with no hardware/mechanical signal -> not_relevant.
    if (
        _SOFTWARE_RE.search(title)
        and _SOFTWARE_RE.search(combined)
        and not _HARDWARE_CUE_RE.search(combined)
    ):
        reasons.append("Pure software role with no mechanical/hardware signal.")
        return ClassificationResult(Classification.NOT_RELEVANT, reasons, 0.8)

    reasons.append("No strong or adjacent mechanical signal detected.")
    return ClassificationResult(Classification.WEAK_MATCH, reasons, 0.3)
