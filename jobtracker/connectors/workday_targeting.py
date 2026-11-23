"""Decide which Workday postings to fetch: student roles in Canada and the US.

Large Workday boards (Airbus, Thales, GE Vernova: 2,000+ postings) are mostly
full-time roles abroad. Workday's own search filters ("facets") let us ask for
exactly the postings that matter:

* a **student facet**: the board's worker-type value for interns / co-ops /
  students (e.g. Airbus "Trainee / Student (Fixed Term)", Magna "Student /
  Co-Op", CAE "COOP-Student (Fixed Term)");
* a **country facet**: Canada and the United States.

Not every employer files every student posting under the student worker type,
so precise keyword searches ("internship", "stagiaire", "coop", ...) restricted
to Canada/US are added too. A keyword that matches most of the board (Workday
treats "intern" like "international" and "co-op" like "co" OR "op") is skipped
because it would not narrow anything.

Small boards are simply listed in full.

The resulting :class:`TargetingPlan` is used both by the direct Workday
connector and, as filtered search URLs, by the Apify fallback when Workday
refuses GitHub's servers. Plans are cached in ``config/workday_filters.json``
so the fallback knows each board's facet ids without being able to reach it.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import quote

logger = logging.getLogger("jobtracker.connectors.workday_targeting")

#: Boards at or below this many postings are listed in full.
SMALL_BOARD = 150
#: A keyword search with at most this many results is always kept (cheap).
KEEP_SMALL_RESULTS = 40
#: Otherwise it is kept only if at least this share of its first page of
#: titles look like student roles. Workday matches "coop" inside
#: "cooperation" and "stage" inside many English titles, so those searches
#: return mostly full-time roles on English boards.
MIN_STUDENT_SHARE = 0.3
#: For a keyword with more results than this, a deeper page is sampled too
#: (Workday ranks the best matches first, so page one always looks good).
DEEP_SAMPLE_OVER = 120
#: Offset of the deeper sample page.
DEEP_SAMPLE_OFFSET = 100
#: The deeper page must look at least this student-heavy to keep the keyword.
MIN_DEEP_STUDENT_SHARE = 0.5
#: Keyword searches tried in addition to the student facet.
KEYWORDS: tuple[str, ...] = (
    "internship",
    "intern",
    "co-op",
    "coop",
    "student",
    "stagiaire",
    "stage",
)

STUDENT_FACET_RE = re.compile(
    r"\b(?:intern(?:ship)?s?|co-?ops?|coop|students?|stagiaires?|stages?|"
    r"trainees?|work[- ]?terms?|placements?)\b",
    re.IGNORECASE,
)
# Titles that look like student roles (used to judge keyword precision).
STUDENT_TITLE_RE = re.compile(
    r"\b(?:intern(?:ship)?s?|co-?ops?|coop|students?|stagiaires?|stages?|"
    r"trainees?|summer|work[- ]?terms?|placements?)\b",
    re.IGNORECASE,
)
# Trades apprenticeships and graduate programs are not co-op terms.
EXCLUDE_FACET_RE = re.compile(r"apprenti|alumni|graduate|retiree", re.IGNORECASE)
COUNTRY_NAMES = {"canada", "united states of america", "united states", "usa"}
_LOCATION_PARAM_RE = re.compile(
    r"location|country|region|state|province|city|hierarchy", re.IGNORECASE
)
_COUNTRY_PARAM_RE = re.compile(r"country|hierarchy1", re.IGNORECASE)

DEFAULT_FILTERS_FILE = "config/workday_filters.json"


def student_share(titles: list[str]) -> float:
    """Share of ``titles`` that look like student roles (0.0 for none)."""
    if not titles:
        return 0.0
    return sum(1 for t in titles if STUDENT_TITLE_RE.search(t or "")) / len(titles)


@dataclass(frozen=True)
class Query:
    """One Workday search: facet filters plus optional keyword."""

    applied_facets: dict[str, tuple[str, ...]]
    search_text: str = ""

    def body(self, *, limit: int, offset: int) -> dict:
        return {
            "limit": limit,
            "offset": offset,
            "appliedFacets": {k: list(v) for k, v in self.applied_facets.items()},
            "searchText": self.search_text,
        }


@dataclass
class TargetingPlan:
    """Which searches cover a board's student postings in Canada/US."""

    full_board: bool
    country: dict[str, list[str]] = field(default_factory=dict)
    student: dict[str, list[str]] = field(default_factory=dict)
    #: Keywords to search (with the country filter). Imprecise ones are
    #: dropped once their first page is seen (see :meth:`skip_keyword`).
    keywords: list[str] = field(default_factory=lambda: list(KEYWORDS))
    #: Postings in Canada/US (or the whole board without a country facet).
    reference_total: int | None = None

    def queries(self) -> list[Query]:
        """The searches to run, student facets first."""
        if self.full_board:
            return [Query({}, "")]
        country = {k: tuple(v) for k, v in self.country.items()}
        out = [
            Query({param: tuple(ids), **country}, "")
            for param, ids in self.student.items()
        ]
        out.extend(Query(dict(country), kw) for kw in self.keywords)
        return out

    @staticmethod
    def skip_keyword(total: int | None, titles: list[str]) -> bool:
        """True when a keyword search mostly returns non-student roles.

        Small result sets are always kept. Larger ones are kept only when at
        least :data:`MIN_STUDENT_SHARE` of the first page's titles look like
        student roles.
        """
        if not isinstance(total, int) or total <= KEEP_SMALL_RESULTS or not titles:
            return False
        return student_share(titles) < MIN_STUDENT_SHARE

    def to_dict(self) -> dict:
        return {
            "full_board": self.full_board,
            "country": self.country,
            "student": self.student,
            "keywords": self.keywords,
            "reference_total": self.reference_total,
        }

    @classmethod
    def from_dict(cls, data: dict) -> TargetingPlan:
        return cls(
            full_board=bool(data.get("full_board")),
            country={k: list(v) for k, v in (data.get("country") or {}).items()},
            student={k: list(v) for k, v in (data.get("student") or {}).items()},
            keywords=list(data.get("keywords") or KEYWORDS),
            reference_total=data.get("reference_total"),
        )


def flatten_facets(payload: object) -> dict[str, list[dict]]:
    """Map facet parameter -> list of ``{"id", "descriptor", "count"}`` values.

    Workday nests some facets (``locationMainGroup`` -> ``locationCountry``);
    nested values are filed under their own ``facetParameter``.
    """
    out: dict[str, list[dict]] = {}

    def walk(param: str | None, values: list) -> None:
        for value in values or []:
            if not isinstance(value, dict):
                continue
            if isinstance(value.get("values"), list):
                walk(value.get("facetParameter") or param, value["values"])
            elif param and value.get("id"):
                out.setdefault(param, []).append(value)

    facets = payload.get("facets") if isinstance(payload, dict) else None
    for facet in facets or []:
        if isinstance(facet, dict):
            walk(facet.get("facetParameter"), facet.get("values") or [])
    return out


def build_plan(first_page: dict) -> TargetingPlan:
    """Build a plan from the board's first (unfiltered) search response."""
    total = first_page.get("total") if isinstance(first_page, dict) else None
    if not isinstance(total, int) or total <= SMALL_BOARD:
        return TargetingPlan(full_board=True, reference_total=total)

    facets = flatten_facets(first_page)
    country: dict[str, list[str]] = {}
    country_total = 0
    for param, values in facets.items():
        if not _COUNTRY_PARAM_RE.search(param):
            continue
        hits = [
            v
            for v in values
            if str(v.get("descriptor", "")).strip().lower() in COUNTRY_NAMES
        ]
        if hits:
            country = {param: [str(v["id"]) for v in hits]}
            country_total = sum(int(v.get("count") or 0) for v in hits)
            break

    student: dict[str, list[str]] = {}
    for param, values in facets.items():
        if _LOCATION_PARAM_RE.search(param):
            continue
        ids = [
            str(v["id"])
            for v in values
            if STUDENT_FACET_RE.search(str(v.get("descriptor", "")))
            and not EXCLUDE_FACET_RE.search(str(v.get("descriptor", "")))
        ]
        if ids:
            student[param] = ids

    return TargetingPlan(
        full_board=False,
        country=country,
        student=student,
        reference_total=country_total or total,
    )


def public_search_urls(base: str, site: str, plan: TargetingPlan) -> list[str]:
    """Public Workday search URLs equivalent to ``plan`` (for the Apify Actor).

    The Actor reads facet filters and ``q=`` from the URL query string, so
    each plan query becomes one URL, e.g.
    ``https://ag.wd3.myworkdayjobs.com/en-US/Airbus?workerSubType=...``.
    """
    root = f"{base.rstrip('/')}/en-US/{site}"
    urls: list[str] = []
    for query in plan.queries():
        parts: list[str] = []
        if query.search_text:
            parts.append(f"q={quote(query.search_text)}")
        for param, ids in query.applied_facets.items():
            parts.extend(f"{quote(param)}={quote(i)}" for i in ids)
        urls.append(root + ("?" + "&".join(parts) if parts else ""))
    return urls


def load_plans(path: str | Path = DEFAULT_FILTERS_FILE) -> dict[str, TargetingPlan]:
    """Cached plans by company id (missing or unreadable file -> empty)."""
    file = Path(path)
    if not file.exists():
        return {}
    try:
        data = json.loads(file.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        logger.warning("Could not read %s; ignoring cached Workday filters.", file)
        return {}
    boards = data.get("boards") if isinstance(data, dict) else None
    if not isinstance(boards, dict):
        return {}
    return {
        cid: TargetingPlan.from_dict(plan)
        for cid, plan in boards.items()
        if isinstance(plan, dict)
    }


def sweep_requested_at(path: str | Path = DEFAULT_FILTERS_FILE) -> float | None:
    """Unix time of the last "check every Workday board now" request.

    Stored as ``sweep_requested_at`` (ISO time) in the filters file. Every
    board not swept since then is swept on the next run, whatever the usual
    spacing. A commit is used (not a workflow input) so the request cannot be
    lost to the self-chaining runs replacing each other in the queue.
    """
    from datetime import datetime

    file = Path(path)
    if not file.exists():
        return None
    try:
        data = json.loads(file.read_text(encoding="utf-8"))
        value = data.get("sweep_requested_at") if isinstance(data, dict) else None
        return datetime.fromisoformat(str(value)).timestamp() if value else None
    except (OSError, ValueError, json.JSONDecodeError):
        return None


def save_plans(
    plans: dict[str, TargetingPlan], path: str | Path = DEFAULT_FILTERS_FILE
) -> Path:
    file = Path(path)
    file.parent.mkdir(parents=True, exist_ok=True)
    previous: dict = {}
    if file.exists():
        try:
            previous = json.loads(file.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            previous = {}
    payload = {
        "about": (
            "Workday search filters per company: student worker types and "
            "Canada/US country ids. Regenerate with "
            "`python -m jobtracker workday-filters` from a network Workday "
            "answers."
        ),
        "boards": {cid: plans[cid].to_dict() for cid in sorted(plans)},
    }
    if isinstance(previous, dict) and previous.get("sweep_requested_at"):
        payload["sweep_requested_at"] = previous["sweep_requested_at"]
    file.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    return file
