"""Turn user 'wrong match' reports into actionable filter-improvement signals.

The static site's 'Report wrong match' button opens a pre-filled GitHub issue
labelled ``job-feedback`` (title + the user's reason + the job's details). This
service reads those issues and classifies each into a concrete, deterministic
follow-up so the filters actually improve and the same kind of posting stops
slipping through -- closing the feedback loop the user asked for.

It is intentionally PURE over a list of issue dicts (no network here): the CLI
fetches the issues via ``gh``/REST and hands them in, and tests pass fixtures.
The output is a structured :class:`FeedbackReport` the maintainer/agent acts on
(e.g. flag a company itar_restricted, add a title keyword to a Stage-1 rule, or
feed a negative example to the AI prompt).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

# Signal -> the concrete filter action it implies. Ordered by specificity.
_SIGNALS: list[tuple[str, re.Pattern[str], str]] = [
    (
        "us_citizenship",
        re.compile(
            r"\b(us|u\.s\.?|american)\s+citizen|citizenship|itar|clearance|"
            r"green\s*card|security\s+clearance|export\s+control\b",
            re.IGNORECASE,
        ),
        "Mark the company itar_restricted / hard-exclude US-located roles.",
    ),
    (
        "seniority",
        re.compile(
            r"\bsenior|sr\.?|staff|principal|lead|years?\s+of\s+experience\b",
            re.IGNORECASE,
        ),
        "Extend the Stage-1 seniority/experience rule with this title/phrasing.",
    ),
    (
        "degree",
        re.compile(
            r"\bmaster'?s|phd|mba|completed\s+degree|graduate\s+degree"
            r"|bachelor'?s\s+required\b",
            re.IGNORECASE,
        ),
        "Extend the Stage-1 advanced/completed-degree rule.",
    ),
    (
        "full_time",
        re.compile(
            r"\bfull[-\s]?time|not\s+an?\s+(intern|co-?op)|permanent\s+role\b",
            re.IGNORECASE,
        ),
        "Treat this title/pattern as a non-co-op professional role.",
    ),
    (
        "new_grad",
        re.compile(
            r"\bnew\s*grad|entry[-\s]?level\s+(?!co-?op)|graduate\s+program\b",
            re.IGNORECASE,
        ),
        "Extend the Stage-1 new-grad rule.",
    ),
    (
        "wrong_field",
        re.compile(
            r"\bsoftware|electrical|not\s+mechanical|finance|sales|marketing|hr\b",
            re.IGNORECASE,
        ),
        "Strengthen the field/classification negative signals (or AI prompt).",
    ),
]


@dataclass
class FeedbackItem:
    """One classified 'wrong match' report."""

    issue_number: int | None
    title: str
    reason: str
    job_id: str | None
    company: str | None
    signals: list[str] = field(default_factory=list)
    suggested_actions: list[str] = field(default_factory=list)


@dataclass
class FeedbackReport:
    """Aggregate, actionable view over all job-feedback issues."""

    items: list[FeedbackItem] = field(default_factory=list)
    #: signal -> count, so the most impactful filter fix is obvious.
    signal_counts: dict[str, int] = field(default_factory=dict)
    #: companies named across reports -> count (candidates for itar/stub flags).
    company_counts: dict[str, int] = field(default_factory=dict)


_JOB_ID_RE = re.compile(r"job_id:\s*([A-Za-z0-9-]+)", re.IGNORECASE)
_COMPANY_RE = re.compile(r"Company:\s*(.+)", re.IGNORECASE)


def _extract(field_re: re.Pattern[str], body: str) -> str | None:
    m = field_re.search(body or "")
    return m.group(1).strip() if m else None


def classify_issue(issue: dict) -> FeedbackItem:
    """Classify one GitHub issue dict into a :class:`FeedbackItem`."""
    title = str(issue.get("title") or "")
    body = str(issue.get("body") or "")
    text = f"{title}\n{body}"

    signals: list[str] = []
    actions: list[str] = []
    for name, pattern, action in _SIGNALS:
        if pattern.search(text):
            signals.append(name)
            actions.append(action)

    return FeedbackItem(
        issue_number=issue.get("number"),
        title=title,
        reason=body.strip(),
        job_id=_extract(_JOB_ID_RE, body),
        company=_extract(_COMPANY_RE, body),
        signals=signals,
        suggested_actions=actions,
    )


def build_report(issues: list[dict]) -> FeedbackReport:
    """Aggregate all job-feedback issues into an actionable report."""
    report = FeedbackReport()
    for issue in issues:
        item = classify_issue(issue)
        report.items.append(item)
        for s in item.signals:
            report.signal_counts[s] = report.signal_counts.get(s, 0) + 1
        if item.company:
            key = item.company.strip()
            report.company_counts[key] = report.company_counts.get(key, 0) + 1
    return report
