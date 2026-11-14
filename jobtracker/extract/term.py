"""Deterministic term, duration, and season extraction (design 8.1).

Every employer names work terms differently, so this module maps whatever the
posting says onto the STUDENT'S three Waterloo co-op terms, defined by months:

* ``winter`` -- January to April
* ``spring`` -- May to August   (what most employers call "Summer")
* ``fall``   -- September to December

Mapping rules, most reliable first:

1. Explicit months ("May - August 2027", "January to April", "starting
   September 2027" + a duration) -> every term those months touch.
2. Season words, only when used as a term ("Summer 2027", "Fall co-op",
   "Spring/Summer 2027 internship") -- a bare "spring" in a mechanical
   description (a coil spring) is ignored:
     - "summer"        -> spring (May-Aug)
     - "fall"/"autumn" -> fall
     - "winter"        -> winter
     - "spring"        -> depends on where the job is. US semesters call
       January-May "Spring", so a US "Spring 2027" internship is the student's
       WINTER term; Canadian co-op programs call May-August "Spring". When the
       country is unknown the posting is ambiguous, so both are recorded and
       the AI stage resolves it.

A posting can cover several terms ("Spring/Summer 2027", "8-month Jan-Aug"),
so :attr:`TermExtraction.terms` is a list. ``term_season`` keeps a single
representative value for older callers. Nothing is guessed: with no signal
the list is empty and the season is ``unknown``.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import UTC, datetime

from jobtracker.schemas.job import TermSeason

#: Canonical student terms, in calendar order.
CANONICAL_TERMS: tuple[str, ...] = ("winter", "spring", "fall")

# Month name -> month number (full + common abbreviations).
_MONTHS: dict[str, int] = {
    "january": 1, "jan": 1, "february": 2, "feb": 2, "march": 3, "mar": 3,
    "april": 4, "apr": 4, "may": 5, "june": 6, "jun": 6, "july": 7, "jul": 7,
    "august": 8, "aug": 8, "september": 9, "sep": 9, "sept": 9,
    "october": 10, "oct": 10, "november": 11, "nov": 11,
    "december": 12, "dec": 12,
    # French (Quebec employers)
    "janvier": 1, "février": 2, "fevrier": 2, "mars": 3, "avril": 4, "mai": 5,
    "juin": 6, "juillet": 7, "août": 8, "aout": 8, "septembre": 9,
    "octobre": 10, "novembre": 11, "décembre": 12, "decembre": 12,
}
_MONTH_ALT = "|".join(sorted(_MONTHS, key=len, reverse=True))

_TERM_RANGES: dict[str, tuple[int, int, int, int]] = {
    # (start_month, start_day, end_month, end_day)
    "winter": (1, 1, 4, 30),
    "spring": (5, 1, 8, 31),
    "fall": (9, 1, 12, 31),
}

_SEASON_WORD = r"spring|summer|fall|autumn|winter|été|automne|hiver|printemps"
# A season word used AS A TERM: followed by a year / term-ish noun, possibly as
# a pair ("Spring/Summer 2027", "Fall & Winter co-op").
_SEASON_TERM_RE = re.compile(
    rf"\b(?P<a>{_SEASON_WORD})\b"
    rf"(?:\s*(?:/|&|and|or|-|–)\s*(?P<b>{_SEASON_WORD})\b)?"
    r"\s*(?:20\d\d|'?\d\d\b|term|terms|co-?op|coop|intern(?:ship)?s?|semester|"
    r"session|work\s+term|placement|student|start|stage)",
    re.IGNORECASE,
)
_YEAR_SEASON_RE = re.compile(rf"\b20\d\d\s+(?P<a>{_SEASON_WORD})\b", re.IGNORECASE)
_MONTH_RANGE_RE = re.compile(
    rf"\b(?P<a>{_MONTH_ALT})\.?(?:\s+\d{{1,2}}(?:st|nd|rd|th)?)?(?:,?\s*20\d\d)?"
    r"\s*(?:-|–|—|to|through|until|thru|au|à)\s*"
    rf"(?P<b>{_MONTH_ALT})\b",
    re.IGNORECASE,
)
_START_RE = re.compile(
    r"\b(?:beginning|starting|start(?:s|ing)?(?:\s+date)?|commencing|begins?|"
    r"available)\b[^.\n]{0,25}?"
    rf"\b(?P<m>{_MONTH_ALT})\.?(?:\s+\d{{1,2}}(?:st|nd|rd|th)?)?,?\s+20\d\d\b",
    re.IGNORECASE,
)
_WORD_NUM = {
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7,
    "eight": 8, "nine": 9, "ten": 10, "eleven": 11, "twelve": 12,
    "sixteen": 16, "eighteen": 18,
}
_DURATION_RE = re.compile(
    r"\b(?P<n>\d{1,2}|" + "|".join(_WORD_NUM) + r")(?:\s*(?:to|-|–)\s*\d{1,2})?"
    r"[\s-]*(?:month|mois)s?\b",
    re.IGNORECASE,
)
_YEAR_RE = re.compile(r"\b(20\d\d)\b")

_SEASON_NORMAL = {
    "summer": "summer", "été": "summer",
    "fall": "fall", "autumn": "fall", "automne": "fall",
    "winter": "winter", "hiver": "winter",
    "spring": "spring", "printemps": "spring",
}


def _month_term(month: int) -> str:
    if month <= 4:
        return "winter"
    if month <= 8:
        return "spring"
    return "fall"


def _months_between(start: int, end: int) -> list[int]:
    months = [start]
    m = start
    while m != end and len(months) < 12:
        m = 1 if m == 12 else m + 1
        months.append(m)
    return months


def season_word_terms(word: str, country: str | None) -> list[str]:
    """Map an employer's season word to the student's canonical term(s)."""
    season = _SEASON_NORMAL.get(word.lower(), "")
    if season == "summer":
        return ["spring"]
    if season in ("fall", "winter"):
        return [season]
    if season == "spring":
        where = (country or "").strip().lower()
        if where in ("united states", "usa", "us"):
            return ["winter"]  # US "Spring semester" = January-May
        if where == "canada":
            return ["spring"]  # Canadian co-op "Spring" = May-August
        return ["winter", "spring"]  # unknown: ambiguous, AI resolves
    return []


def normalize_profile_term(term: str) -> str | None:
    """Map a profile term name ("Spring", "Summer", "Autumn") to canonical."""
    low = (term or "").strip().lower()
    if low in ("spring", "summer", "été", "printemps"):
        return "spring"
    if low in ("fall", "autumn", "automne"):
        return "fall"
    if low in ("winter", "hiver"):
        return "winter"
    return None


def term_set(value: str | None) -> set[str]:
    """Parse a stored ``term_season`` ("spring" / "winter,spring") into a set."""
    if not value or value == TermSeason.UNKNOWN.value:
        return set()
    return {t.strip() for t in value.split(",") if t.strip() in CANONICAL_TERMS}


@dataclass
class TermExtraction:
    """Result of term/duration extraction (all evidence-carrying)."""

    term_season: TermSeason = TermSeason.UNKNOWN
    terms: list[str] = field(default_factory=list)
    term_start_date: datetime | None = None
    term_end_date: datetime | None = None
    duration_months: int | None = None
    duration_label: str = ""
    evidence: str = ""

    @property
    def stored_value(self) -> str:
        """The value persisted in ``Job.term_season`` (comma-joined terms)."""
        return ",".join(self.terms) if self.terms else TermSeason.UNKNOWN.value


def _duration(text: str) -> tuple[int, str] | None:
    match = _DURATION_RE.search(text)
    if match is None:
        return None
    raw = match.group("n").lower()
    months = _WORD_NUM.get(raw) if not raw.isdigit() else int(raw)
    if not months or months <= 0 or months > 24:
        return None
    return months, match.group(0).strip()


# Season/month mentions inside a graduation-date sentence ("Expected graduation
# between Fall 2027 and Summer 2028") describe the candidate, not the work term.
_GRAD_CONTEXT_RE = re.compile(
    r"graduat|degree\s+completion|complete\s+(?:your|their)\s+degree|"
    r"class\s+of|commencement",
    re.IGNORECASE,
)


def _strip_graduation_sentences(text: str) -> str:
    parts = re.split(r"(?<=[.!?\n])", text)
    return "".join(p for p in parts if not _GRAD_CONTEXT_RE.search(p))


def extract_term(
    title: str, description: str = "", *, country: str | None = None
) -> TermExtraction:
    """Extract the canonical term(s), dates, and duration from a posting.

    The TITLE is authoritative: when it names a term ("Intern [Summer 2027]")
    the description is not consulted for terms, so incidental mentions in the
    body cannot add extra terms. Graduation-date sentences are ignored.
    """
    title = title or ""
    if description:
        from_title = extract_term(title, "", country=country)
        if from_title.terms:
            body = _extract(f"{title}\n{description}", country)
            from_title.duration_months = body.duration_months
            from_title.duration_label = body.duration_label
            return from_title
    return _extract(
        f"{title}\n{_strip_graduation_sentences(description or '')}".strip(),
        country,
    )


def _extract(text: str, country: str | None) -> TermExtraction:
    result = TermExtraction()
    found: list[str] = []
    evidence: list[str] = []
    start_month: int | None = None
    end_month: int | None = None

    dur = _duration(text)
    if dur is not None:
        result.duration_months, ev = dur
        result.duration_label = f"{dur[0]}-month"
        evidence.append(ev)

    # 1) Explicit month ranges (most reliable).
    for match in _MONTH_RANGE_RE.finditer(text):
        # "may" is also an English verb ("you may - at times - travel"); only
        # accept it as a month when capitalised.
        if any(
            match.group(k).lower() == "may" and match.group(k) != "May"
            for k in ("a", "b")
        ):
            continue
        start = _MONTHS[match.group("a").lower()]
        end = _MONTHS[match.group("b").lower()]
        span = _months_between(start, end)
        if len(span) > 9:  # "Jan - Dec" style calendars, not a work term
            continue
        if start_month is None:
            start_month, end_month = start, end
        for month in span:
            found.append(_month_term(month))
        evidence.append(match.group(0).strip())

    # 2) "Starting September 2027" (+ duration, default one 4-month term).
    if not found:
        start_match = _START_RE.search(text)
        if start_match is not None:
            start = _MONTHS[start_match.group("m").lower()]
            length = result.duration_months or 4
            end = (start + length - 2) % 12 + 1
            start_month, end_month = start, end
            for month in _months_between(start, end):
                found.append(_month_term(month))
            evidence.append(start_match.group(0).strip())

    # 3) Season words used as a term.
    if not found:
        for match in list(_SEASON_TERM_RE.finditer(text)) + list(
            _YEAR_SEASON_RE.finditer(text)
        ):
            for key in ("a", "b"):
                word = match.groupdict().get(key)
                if word:
                    found.extend(season_word_terms(word, country))
            evidence.append(match.group(0).strip())

    terms = [t for t in CANONICAL_TERMS if t in found]
    result.terms = terms
    result.evidence = "; ".join(evidence)
    if terms:
        primary = terms[0] if len(terms) == 1 else (
            "spring" if "spring" in terms else terms[0]
        )
        result.term_season = TermSeason(primary)
        year_match = _YEAR_RE.search(text)
        if year_match:
            year = int(year_match.group(1))
            if start_month is not None:
                s_month, s_day = start_month, 1
                e_month = end_month or start_month
                e_year = year + (1 if e_month < s_month else 0)
                e_day = 30 if e_month in (4, 6, 9, 11) else (28 if e_month == 2 else 31)
            else:
                s_month, s_day, _, _ = _TERM_RANGES[terms[0]]
                _, _, e_month, e_day = _TERM_RANGES[terms[-1]]
                e_year = year
            result.term_start_date = datetime(year, s_month, s_day, tzinfo=UTC)
            result.term_end_date = datetime(e_year, e_month, e_day, tzinfo=UTC)
    return result
