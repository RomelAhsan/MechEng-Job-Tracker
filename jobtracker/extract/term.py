"""Deterministic term, duration, and season extraction (design 8.1).

Pure functions, no network, no guessing. Given a job's title + description the
pipeline detects a co-op/internship term season, start/end dates, and duration,
recording the exact matched substring as evidence. When nothing can be
determined the season is ``unknown`` (never guessed) and ``evidence`` is empty.

Season -> date range per the SPEC "Supported terms" table (year supplied by the
posting, else left unresolved):

* Spring / Summer: May 1 -- Aug 31
* Fall: Sep 1 -- Dec 31
* Winter: Jan 1 -- Apr 30
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, datetime

from jobtracker.schemas.job import TermSeason

# Month name -> month number (full + common 3-letter abbreviations).
_MONTHS: dict[str, int] = {
    "january": 1, "jan": 1,
    "february": 2, "feb": 2,
    "march": 3, "mar": 3,
    "april": 4, "apr": 4,
    "may": 5,
    "june": 6, "jun": 6,
    "july": 7, "jul": 7,
    "august": 8, "aug": 8,
    "september": 9, "sep": 9, "sept": 9,
    "october": 10, "oct": 10,
    "november": 11, "nov": 11,
    "december": 12, "dec": 12,
}

# Season end day (end month -> last day) for the SPEC ranges.
_SEASON_RANGES: dict[TermSeason, tuple[int, int, int, int]] = {
    # (start_month, start_day, end_month, end_day)
    TermSeason.SPRING: (5, 1, 8, 31),
    TermSeason.SUMMER: (5, 1, 8, 31),
    TermSeason.FALL: (9, 1, 12, 31),
    TermSeason.WINTER: (1, 1, 4, 30),
}

# Which season a given month falls into (for month-range -> season mapping).
_MONTH_TO_SEASON: dict[int, TermSeason] = {
    1: TermSeason.WINTER, 2: TermSeason.WINTER, 3: TermSeason.WINTER,
    4: TermSeason.WINTER,
    5: TermSeason.SPRING, 6: TermSeason.SPRING, 7: TermSeason.SPRING,
    8: TermSeason.SPRING,
    9: TermSeason.FALL, 10: TermSeason.FALL, 11: TermSeason.FALL,
    12: TermSeason.FALL,
}

_MONTH_ALT = "|".join(sorted(_MONTHS, key=len, reverse=True))

_SEASON_RE = re.compile(
    r"\b(spring|summer|fall|autumn|winter)\b(?:\s+(?:co-?op|work\s+term|term|internship|intern))?"
    r"(?:\s+(\d{4}))?",
    re.IGNORECASE,
)
# Season may also appear *after* a year ("2027 Spring").
_YEAR_SEASON_RE = re.compile(
    r"\b(\d{4})\s+(spring|summer|fall|autumn|winter)\b", re.IGNORECASE
)
_MONTH_RANGE_RE = re.compile(
    rf"\b({_MONTH_ALT})\s*(?:-|–|to|through|until|[-–]|/)\s*({_MONTH_ALT})\b"
    r"(?:[,\s]+(\d{4}))?",
    re.IGNORECASE,
)
_BEGINNING_RE = re.compile(
    rf"\b(?:beginning|starting|start(?:ing|s)?|commencing)\s+({_MONTH_ALT})\s+(\d{{4}})\b",
    re.IGNORECASE,
)
_DURATION_RE = re.compile(
    r"\b(\d{1,2})[\s-]*month[s]?\b",
    re.IGNORECASE,
)


@dataclass
class TermExtraction:
    """Result of term/duration extraction (all evidence-carrying)."""

    term_season: TermSeason = TermSeason.UNKNOWN
    term_start_date: datetime | None = None
    term_end_date: datetime | None = None
    duration_months: int | None = None
    duration_label: str = ""
    evidence: str = ""


def _season_from_word(word: str) -> TermSeason:
    lowered = word.lower()
    if lowered == "autumn":
        return TermSeason.FALL
    return TermSeason(lowered)


def _start_date(season: TermSeason, year: int | None) -> datetime | None:
    if year is None or season not in _SEASON_RANGES:
        return None
    start_month, start_day, _, _ = _SEASON_RANGES[season]
    return datetime(year, start_month, start_day, tzinfo=UTC)


def _end_date(season: TermSeason, year: int | None) -> datetime | None:
    if year is None or season not in _SEASON_RANGES:
        return None
    _, _, end_month, end_day = _SEASON_RANGES[season]
    return datetime(year, end_month, end_day, tzinfo=UTC)


def _match_season(text: str) -> tuple[TermSeason, int | None, str] | None:
    """Return (season, year, evidence) from an explicit season word."""
    match = _SEASON_RE.search(text)
    year_season = _YEAR_SEASON_RE.search(text)
    if match is None and year_season is None:
        return None
    if match is not None:
        season = _season_from_word(match.group(1))
        year = int(match.group(2)) if match.group(2) else None
        return season, year, match.group(0).strip()
    season = _season_from_word(year_season.group(2))
    return season, int(year_season.group(1)), year_season.group(0).strip()


def _match_month_range(text: str) -> tuple[TermSeason, int | None, str] | None:
    """Return (season, year, evidence) from a month range like 'May-August'."""
    match = _MONTH_RANGE_RE.search(text)
    if match is None:
        return None
    start_month = _MONTHS[match.group(1).lower()]
    year = int(match.group(3)) if match.group(3) else None
    season = _MONTH_TO_SEASON[start_month]
    return season, year, match.group(0).strip()


def _match_beginning(text: str) -> tuple[TermSeason, int | None, str] | None:
    """Return (season, year, evidence) from 'beginning May 2027' style phrases."""
    match = _BEGINNING_RE.search(text)
    if match is None:
        return None
    month = _MONTHS[match.group(1).lower()]
    year = int(match.group(2))
    season = _MONTH_TO_SEASON[month]
    return season, year, match.group(0).strip()


def _match_duration(text: str) -> tuple[int, str] | None:
    """Return (duration_months, evidence) from a duration phrase."""
    match = _DURATION_RE.search(text)
    if match is None:
        return None
    months = int(match.group(1))
    if months <= 0 or months > 24:
        return None
    return months, match.group(0).strip()


def extract_term(title: str, description: str = "") -> TermExtraction:
    """Extract term season, dates, and duration from a job's text.

    Explicit season words win; otherwise a month range or a 'beginning <month>
    <year>' phrase is mapped to a season via the SPEC table. Duration is parsed
    independently. Nothing is guessed: with no signal the season stays
    ``unknown`` and ``evidence`` is empty.
    """
    text = f"{title}\n{description}".strip()
    result = TermExtraction()
    evidence_parts: list[str] = []

    season_hit = (
        _match_season(text)
        or _match_month_range(text)
        or _match_beginning(text)
    )
    if season_hit is not None:
        season, year, evidence = season_hit
        result.term_season = season
        result.term_start_date = _start_date(season, year)
        result.term_end_date = _end_date(season, year)
        evidence_parts.append(evidence)

    duration_hit = _match_duration(text)
    if duration_hit is not None:
        months, evidence = duration_hit
        result.duration_months = months
        result.duration_label = f"{months}-month"
        evidence_parts.append(evidence)

    result.evidence = "; ".join(evidence_parts)
    return result
