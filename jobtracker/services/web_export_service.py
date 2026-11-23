"""Static JSON feed for the always-on React site (GitHub Pages).

The hosted site is a client-rendered React SPA served as static files, so it
cannot run any server code. This module produces the one data file it reads:
``jobs.json``, containing the user's final eligible "Relevant For Me" set.

The eligible set is NOT recomputed here. We call the existing
:func:`dashboard_service.list_relevant_jobs`, which is the single source of
truth: Stage-1 deterministic hard filter (full-time/new-grad, US-citizen-only /
ITAR / clearance, advanced/completed degree, senior / N+years, explicitly
incompatible term or duration all removed) INTERSECT the Stage-2 AI-eligible
gate, with the documented no-key fallback to the Stage-1 survivors so the feed
is never empty just because no Gemini key is configured. The React app only
renders and client-filters this set; it never re-implements the screening.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from urllib.parse import urlsplit

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from jobtracker.db.models import Company, Job, SourceHealth
from jobtracker.normalize.posting_date import RELATIVE_LOWER_BOUND_DAYS
from jobtracker.services import dashboard_service
from jobtracker.services.dashboard_service import JobView, humanize_ago
from jobtracker.services.ingest_service import plain_reason

logger = logging.getLogger("jobtracker.services.web_export")

#: Schema version of the emitted feed, so the front-end can guard against a
#: stale/incompatible shape after a future change.
FEED_VERSION = 1

#: Freshness cutoff: a job older than this is hidden from the eligible feed.
#: "Older" is measured from the best available date (true posting date first,
#: then how long it has sat on our page). This is a hard hide (not a toggle);
#: a job whose age cannot be determined at all is KEPT rather than hidden.
#: A role whose work term has not started yet is NEVER hidden by age (summer
#: internships are posted months ahead); the link checker removes it once the
#: employer closes it.
MAX_AGE = timedelta(days=14)

#: User-reported dead links, maintained by the report-job workflow.
REPORTED_FILE = "config/reported_jobs.json"


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


def _as_aware(value: datetime) -> datetime:
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


def best_posted_date(view: JobView) -> datetime | None:
    """Best available date for freshness, in priority order.

    (1) the TRUE posting date from the source (``posted_at``), else
    (2) how long the posting has sat on our page (``first_seen_at``). Returns
    ``None`` when neither is known (age is then indeterminate).
    """
    for value in (view.posted_at, view.first_seen_at):
        if value is not None:
            return _as_aware(value)
    return None


def posted_days_ago(view: JobView, *, now: datetime | None = None) -> int | None:
    """Whole days since the best-available posting date (>= 0), or ``None``.

    Used both for the freshness cutoff and the "Posted X days ago" label on
    every card. Prefers the true posting date over the fetch time, matching the
    user's request to measure time since the ACTUAL posting.
    """
    date = best_posted_date(view)
    if date is None:
        return None
    now = now or datetime.now(UTC)
    days = (now - date).days
    return days if days > 0 else 0


def posted_days_ago_is_lower_bound(view: JobView, *, now: datetime | None = None) -> bool:
    """True when the posting age is only known as a lower bound ("30+ days").

    Workday's "Posted 30+ Days Ago" string resolves to a true posting date of
    now - :data:`RELATIVE_LOWER_BOUND_DAYS` (so the 14-day freshness cutoff
    still hides it), but the exact age is unknown. We recognize that case here,
    from the posting-date-derived day count, so the website can render
    "Posted 30+ days ago" rather than a precise but wrong "Posted 30 days ago".
    See :mod:`jobtracker.normalize.posting_date` for why the lower-bound flag
    is derived at the presentation layer instead of threaded through the DB.

    Only applies when the true posting date drives the count; a job whose age
    comes from ``first_seen_at`` (no source posting date) is an exact value.
    """
    if view.posted_at is None:
        return False
    days = posted_days_ago(view, now=now)
    return days is not None and days >= RELATIVE_LOWER_BOUND_DAYS


def term_not_started(view: JobView, *, now: datetime | None = None) -> bool:
    """True when the posting names a work term that has not started yet.

    With a stated year the term start date decides. A term named without a
    year ("Winter co-op") is treated as upcoming: postings are taken down once
    the term is filled, and the link checker removes closed ones.
    """
    from jobtracker.extract.term import term_set

    now = now or datetime.now(UTC)
    start = view.term_start_date
    if start is not None:
        return _as_aware(start) > now
    return bool(term_set(view.term_season))


def is_fresh(view: JobView, *, now: datetime | None = None) -> bool:
    """True when a job should stay on the page as far as age is concerned.

    * A role whose term has not started is always kept (Option A).
    * Otherwise a job older than :data:`MAX_AGE` by its best-available date is
      stale and hidden. A job with no determinable date is KEPT.
    """
    now = now or datetime.now(UTC)
    if term_not_started(view, now=now):
        return True
    date = best_posted_date(view)
    if date is None:
        return True
    return now - date <= MAX_AGE


def posted_date_source(view: JobView) -> str | None:
    """Where the "posted" age comes from: "employer" (the job board's own
    posting date) or "first_seen" (no date given; when we first saw it)."""
    if view.posted_at is not None:
        return "employer"
    if view.first_seen_at is not None:
        return "first_seen"
    return None


def load_reported(path: str | Path = REPORTED_FILE) -> dict[str, dict]:
    """Jobs the user reported as dead links, keyed by job_id."""
    file = Path(path)
    if not file.exists():
        return {}
    try:
        data = json.loads(file.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        logger.warning("Could not read %s; ignoring reported jobs.", file)
        return {}
    items = data.get("jobs") if isinstance(data, dict) else None
    if not isinstance(items, list):
        return {}
    return {
        str(item["job_id"]): item
        for item in items
        if isinstance(item, dict) and item.get("job_id")
    }


#: SPEC fields of a cached AI analysis that we surface as "eligibility
#: reasoning" on an AI-approved card. Kept to the human-readable ones.
_AI_REASONING_FIELDS = (
    "recommendation",
    "relevance_score",
    "confidence",
    "explanation",
    "mechanical_relevance",
    "term_assessment",
    "duration_assessment",
    "location_assessment",
    "work_authorization_assessment",
    "matched_skills",
    "missing_skills",
    "concerns",
)


def _ai_reasoning(result: dict | None) -> dict | None:
    """Extract the human-readable AI reasoning from a cached analysis result.

    Returns ``None`` when the job has no cached AI analysis (e.g. not yet
    screened by AI, or running in the no-key Stage-1 fallback), so the front-end
    only shows the "AI eligibility reasoning" button on genuinely AI-assessed
    cards.
    """
    if not result:
        return None
    reasoning = {k: result[k] for k in _AI_REASONING_FIELDS if k in result}
    return reasoning or None


def _job_to_dict(view: JobView, ai_result: dict | None) -> dict[str, object]:
    """Serialize one eligible job into the flat shape the React app consumes."""
    return {
        "job_id": view.job_id,
        "title": view.title,
        "company": view.company_name,
        "location": view.location,
        "country": view.country,
        "remote_status": view.remote_status,
        "employment_type": view.employment_type,
        "term_season": view.term_season,
        "duration_months": view.duration_months,
        "duration_label": view.duration_label,
        "fit_score": view.fit_score,
        "eligibility_reasons": list(view.eligibility_reasons or []),
        "visa_sponsorship": view.visa_sponsorship,
        "citizenship_requirement": view.citizenship_requirement,
        "concerns": list(view.concerns or []),
        "skills": list(view.skills or []),
        "application_url": view.application_url,
        "source_url": view.source_url,
        "posted_at": _iso(view.posted_at),
        "posted_days_ago": posted_days_ago(view),
        "posted_days_ago_is_lower_bound": posted_days_ago_is_lower_bound(view),
        # "employer" when the job board gave a posting date, "first_seen"
        # when it did not and the age is from when we first saw the job.
        "posted_date_source": posted_date_source(view),
        "first_seen_at": _iso(view.first_seen_at),
        "first_seen_ago": view.first_seen_ago,
        # Link checker result: "open", "cant_check" (never hidden; the reason
        # says why), or None before the first check.
        "link_status": view.link_status,
        "link_checked_at": _iso(view.link_checked_at),
        "link_checked_ago": humanize_ago(view.link_checked_at),
        "cant_check_reason": (
            view.link_check_reason if view.link_status == "cant_check" else None
        ),
        "source_last_checked_at": _iso(view.source_last_checked_at),
        "source_last_checked_ago": view.source_last_checked_ago,
        "is_new": view.is_new,
        # None unless the job was actually assessed by the AI; drives the
        # "AI eligibility reasoning" button on the card.
        "ai_reasoning": _ai_reasoning(ai_result),
    }


def _latest_source_check(session: Session) -> datetime | None:
    """Most recent successful source poll across all companies, if any."""
    stmt = select(SourceHealth.last_checked_at).where(
        SourceHealth.last_checked_at.is_not(None)
    )
    checks = [c for c in session.scalars(stmt) if c is not None]
    return max(checks) if checks else None


@dataclass
class WebFeed:
    """The assembled feed plus a couple of summary counts for logging/tests."""

    payload: dict[str, object]
    job_count: int
    #: Student roles fetched but not shown (written to hidden.json).
    hidden: list[dict[str, object]] = field(default_factory=list)


#: ATS types that are honest "stub" placeholders with no working connector --
#: these employers are NOT actually fetched, so they must not be counted or
#: advertised as "tracked".
_STUB_ATS = {"html"}


def _careers_host(careers_url: str | None) -> str | None:
    """Registrable careers host for a company (``www.`` stripped), or ``None``.

    Used by the front-end's Add-tab duplicate guard so pasting a careers URL
    for an already-tracked company is matched by HOST, not just by display
    name (a display name like "L3Harris Technologies" never parses to a host).
    """
    if not careers_url:
        return None
    raw = careers_url.strip()
    if not raw:
        return None
    split = urlsplit(raw if "//" in raw else f"https://{raw}")
    host = (split.netloc or split.path).split("/", 1)[0].lower()
    host = host.split(":", 1)[0].removeprefix("www.")
    return host or None


def _tracked_companies(session: Session) -> list[dict[str, object]]:
    """The actively-tracked employers we actually pull jobs from.

    Uses the EXACT same predicate as the ``tracked_company_count`` summary
    (active + enabled + a real connector, i.e. not an ``html`` stub) so the
    list and the count never disagree. Each item carries the company name, its
    ATS, a careers ``host`` (derived from ``careers_url`` so the Add tab can
    block a pasted careers URL for an already-tracked company), and the number
    of ACTIVE jobs currently held. Sorted by name (case-insensitive) for
    stable, human-friendly output.
    """
    rows = session.execute(
        select(Company.id, Company.name, Company.ats, Company.careers_url).where(
            Company.active.is_(True),
            Company.enabled.is_(True),
            Company.ats.not_in(_STUB_ATS),
        )
    ).all()

    companies: list[dict[str, object]] = []
    for company_id, name, ats, careers_url in rows:
        job_count = session.scalar(
            select(func.count())
            .select_from(Job)
            .where(Job.company_id == company_id, Job.status == "active")
        )
        companies.append(
            {
                "name": name,
                "ats": ats,
                "host": _careers_host(careers_url),
                "job_count": int(job_count or 0),
            }
        )

    companies.sort(key=lambda c: str(c["name"]).casefold())
    return companies


def shown_views(
    session: Session, *, user_id: int | None = None, now: datetime | None = None
) -> list[JobView]:
    """The jobs the website shows: eligible set, freshness rule, not reported."""
    now = now or datetime.now(UTC)
    reported = load_reported()
    views = dashboard_service.list_relevant_jobs(session, user_id=user_id)
    return [v for v in views if is_fresh(v, now=now) and v.job_id not in reported]


_STUDENT_TITLE_RE = re.compile(
    r"\b(?:intern(?:ship)?s?|co-?ops?|coop|students?|stagiaires?|stages?|"
    r"work[- ]?terms?|placements?|apprentice\w*)\b",
    re.IGNORECASE,
)

#: Plain-English labels for the screening rules, used in the hidden-jobs list.
RULE_LABELS: dict[str, str] = {
    "removed": "Posting was removed",
    "employment_type": "Not a co-op or internship (full-time, contract, ...)",
    "professional_role": "Title is a professional (non-student) role",
    "foreign_country": "Located outside Canada and the US",
    "itar_company": "US defense/aerospace employer: US citizenship required",
    "itar": "Requires US citizenship, ITAR, or a security clearance",
    "citizenship": "Citizenship, permanent residency, or sponsorship requirement",
    "graduation_window": "Graduation window ends before your graduation year",
    "degree": "Requires a completed or graduate degree",
    "experience": "Requires work experience",
    "term": "Work term does not match your terms",
    "duration": "Duration is outside your 3-4 month range",
    "not_relevant": "Not a mechanical-engineering role",
    "ai_rejected": "The AI check found a problem",
    "ai_pending": "Waiting for the AI check",
    "stale": "Older than 14 days and its term has started or is not stated",
    "reported": "You reported the link as dead",
}

#: Order used to pick the main reason when a job has several.
_REASON_ORDER = (
    "reported",
    "foreign_country",
    "itar_company",
    "itar",
    "citizenship",
    "employment_type",
    "professional_role",
    "graduation_window",
    "degree",
    "experience",
    "term",
    "duration",
    "not_relevant",
    "ai_rejected",
    "ai_pending",
    "stale",
    "removed",
)


def _coverage(
    session: Session, shown: list[JobView], hidden: list[dict], now: datetime
) -> list[dict[str, object]]:
    """Per-company check status and counts for the coverage panel."""
    shown_by_company: dict[str, int] = {}
    for view in shown:
        shown_by_company[view.company_id] = shown_by_company.get(view.company_id, 0) + 1
    hidden_by_company: dict[str, dict[str, int]] = {}
    for item in hidden:
        counts = hidden_by_company.setdefault(str(item["company_id"]), {})
        counts[str(item["group"])] = counts.get(str(item["group"]), 0) + 1

    out: list[dict[str, object]] = []
    companies = session.scalars(
        select(Company).where(Company.active.is_(True), Company.enabled.is_(True))
    ).all()
    for company in companies:
        health = session.get(SourceHealth, company.id)
        supported = company.ats not in _STUB_ATS
        last_ok = _as_aware(health.last_successful_check_at) if health and \
            health.last_successful_check_at else None
        if not supported:
            status = "not_supported"
        elif (
            health is None
            or health.last_checked_at is None
            or (health.last_status == "requires_fallback" and not last_ok)
            or (health.last_status == "requires_fallback" and company.ats not in _STUB_ATS
                and not (last_ok and now - last_ok <= timedelta(hours=3)))
        ):
            # Never read with its current connector yet (e.g. just upgraded
            # from an unsupported placeholder).
            status = "never_checked"
        elif health.last_status == "ok" or (last_ok and now - last_ok <= timedelta(hours=3)):
            status = "ok"
        else:
            status = "failing"
        active = session.scalar(
            select(func.count()).select_from(Job).where(
                Job.company_id == company.id, Job.status == "active"
            )
        ) or 0
        hidden_counts = hidden_by_company.get(company.id, {})
        out.append(
            {
                "id": company.id,
                "name": company.name,
                "ats": company.ats,
                "status": status,
                "last_success_at": _iso(last_ok),
                "last_success_ago": humanize_ago(last_ok, now=now),
                "failing_since": _iso(health.failing_since) if health else None,
                "error": (
                    plain_reason(health.last_error) if health and status == "failing" else None
                ),
                "via": health.last_fetch_via if health else None,
                "postings": int(active),
                "shown": shown_by_company.get(company.id, 0),
                "hidden": sum(hidden_counts.values()),
                "hidden_reasons": hidden_counts,
                "careers_url": company.careers_url,
            }
        )
    rank = {"failing": 0, "never_checked": 1, "ok": 2, "not_supported": 3}
    out.sort(key=lambda c: (rank.get(str(c["status"]), 9), str(c["name"]).casefold()))
    return out


def _warnings(coverage: list[dict[str, object]]) -> list[dict[str, object]]:
    """Banner messages for boards that cannot be checked right now."""
    failing = [c for c in coverage if c["status"] == "failing"]
    if not failing:
        return []
    names = ", ".join(str(c["name"]) for c in failing[:6])
    more = f" and {len(failing) - 6} more" if len(failing) > 6 else ""
    return [
        {
            "kind": "boards_failing",
            "count": len(failing),
            "message": (
                f"{len(failing)} {'company' if len(failing) == 1 else 'companies'} "
                f"could not be checked on the last run: {names}{more}. Their jobs "
                "stay on the page with a \"Can't be checked\" tag. See Coverage "
                "for the reason."
            ),
        }
    ]


def _ai_status(session: Session, job: Job, user_id: int | None) -> tuple[str | None, str]:
    """("ai_rejected" | "ai_pending" | None, detail) for an AI-tier job."""
    from jobtracker.screen.ai_verdict import ai_eligibility
    from jobtracker.services import ai_service

    result, is_current = ai_service.latest_verdict(session, job.job_id, user_id=user_id)
    if not result:
        return "ai_pending", "Not checked by the AI yet."
    if not ai_eligibility(result):
        verify = result.get("_verify") if isinstance(result.get("_verify"), dict) else {}
        detail = (
            (verify.get("explanation") if verify.get("eligible") is False else None)
            or result.get("explanation")
            or "The AI marked it not eligible."
        )
        return "ai_rejected", str(detail)
    if not is_current:
        return "ai_pending", "Waiting to be re-checked under the current rules."
    return "ai_pending", "Waiting for the second AI check."


def hidden_jobs(
    session: Session,
    shown: list[JobView],
    *,
    user_id: int | None = None,
    now: datetime | None = None,
) -> list[dict[str, object]]:
    """Every student role we fetched but do not show, with the reason(s).

    "Student role" = the screener did not decide it is a regular job
    (``not_student_role``). Lets the user audit every decision.
    """
    now = now or datetime.now(UTC)
    shown_ids = {v.job_id for v in shown}
    reported = load_reported()
    relevant = set(dashboard_service.RELEVANT_CLASSIFICATIONS)
    rows = session.execute(
        select(Job, Company.name)
        .join(Company, Company.id == Job.company_id)
        .where(Job.status == "active", Company.enabled.is_(True))
    ).all()
    out: list[dict[str, object]] = []
    for job, company_name in rows:
        if job.job_id in shown_ids:
            continue
        failed = list(job.eligibility_failed_rules or [])
        if "not_student_role" in failed:
            continue
        # A regular full-time/new-grad job whose description merely mentions
        # students is not a student role: leave it out unless the TITLE says
        # it is one (keeps the list to postings worth auditing).
        if set(failed) & {"employment_type", "professional_role"} and not (
            _STUDENT_TITLE_RE.search(job.title or "")
        ):
            continue
        reasons: list[dict[str, str]] = []
        texts = list(job.eligibility_reasons or [])
        for i, rule in enumerate(failed):
            reasons.append(
                {
                    "code": rule,
                    "label": RULE_LABELS.get(rule, rule),
                    "detail": (texts[i] if i < len(texts) else "")[:240],
                }
            )
        if job.job_id in reported:
            reasons.insert(
                0, {"code": "reported", "label": RULE_LABELS["reported"], "detail": ""}
            )
        if not failed and job.classification not in relevant:
            detail = (job.classification_reasons or [""])[0]
            reasons.append(
                {"code": "not_relevant", "label": RULE_LABELS["not_relevant"],
                 "detail": str(detail)}
            )
        if not failed and job.classification in relevant and job.job_id not in reported:
            if job.eligible is not False and (job.screening_tier or "ai") == "ai":
                code, detail = _ai_status(session, job, user_id)
                if code:
                    reasons.append(
                        {"code": code, "label": RULE_LABELS[code], "detail": detail}
                    )
            if not reasons:
                reasons.append(
                    {"code": "stale", "label": RULE_LABELS["stale"], "detail": ""}
                )
        if not reasons:
            continue
        codes = [r["code"] for r in reasons]
        group = next((c for c in _REASON_ORDER if c in codes), codes[0])
        audit = job.rule_audit if isinstance(job.rule_audit, dict) else None
        if audit and sorted(audit.get("rules") or []) != sorted(failed):
            audit = None  # the rules changed since this audit
        posted = job.posted_at or job.first_seen_at
        out.append(
            {
                "job_id": job.job_id,
                "company_id": job.company_id,
                "title": job.title or "",
                "company": company_name,
                "location": job.location or "",
                "country": job.country,
                "term_season": job.term_season or "unknown",
                "posted_at": _iso(job.posted_at),
                "posted_days_ago": (
                    max(0, (now - _as_aware(posted)).days) if posted else None
                ),
                "application_url": job.application_url,
                "group": group,
                "reasons": reasons,
                "evidence": [str(e)[:200] for e in (job.eligibility_evidence or [])][:1],
                "ai_audit": (
                    {
                        "ai_eligible": bool(audit.get("ai_eligible")),
                        "explanation": str(audit.get("explanation") or "")[:400],
                    }
                    if audit
                    else None
                ),
            }
        )
    out.sort(key=lambda h: (str(h["company"]).casefold(), str(h["title"]).casefold()))
    return out


def build_feed(session: Session, *, user_id: int | None = None) -> WebFeed:
    """Build the static feed payload from the eligible "Relevant For Me" set."""
    from jobtracker.services import ai_service

    now = datetime.now(UTC)
    fresh_views = shown_views(session, user_id=user_id, now=now)

    jobs: list[dict[str, object]] = []
    for v in fresh_views:
        cached = ai_service.get_cached(session, v.job_id, user_id=user_id)
        jobs.append(_job_to_dict(v, cached.result if cached else None))

    # List only companies we ACTUALLY pull jobs from: active, enabled, and
    # backed by a real connector (not an `html` stub). The LIST and the count
    # share the exact same predicate so they can never disagree.
    tracked_companies = _tracked_companies(session)
    hidden = hidden_jobs(session, fresh_views, user_id=user_id, now=now)
    coverage = _coverage(session, fresh_views, hidden, now)
    hidden_counts: dict[str, int] = {}
    for item in hidden:
        hidden_counts[str(item["group"])] = hidden_counts.get(str(item["group"]), 0) + 1

    payload: dict[str, object] = {
        "version": FEED_VERSION,
        "generated_at": now.isoformat(),
        "source_last_checked_at": _iso(_latest_source_check(session)),
        "tracked_company_count": len(tracked_companies),
        "tracked_companies": tracked_companies,
        "job_count": len(jobs),
        # Ambiguous jobs held back until the AI confirms them (never shown
        # unconfirmed when an AI key is configured).
        "pending_ai_count": dashboard_service.pending_ai_count(session),
        "coverage": coverage,
        "warnings": _warnings(coverage),
        "hidden_count": len(hidden),
        "hidden_counts": hidden_counts,
        "jobs": jobs,
    }
    return WebFeed(payload=payload, job_count=len(jobs), hidden=hidden)


def export_web(
    session: Session,
    *,
    output: str = "web/public/data/jobs.json",
    user_id: int | None = None,
) -> Path:
    """Write the eligible-set feed to ``output`` as pretty JSON.

    Always writes a valid file -- when there are no eligible jobs (empty DB or
    nothing matched) the feed still has ``"jobs": []`` so the deploy pipeline
    and the front-end both have a well-formed file to work with.
    """
    feed = build_feed(session, user_id=user_id)
    out_path = Path(output)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(
        json.dumps(feed.payload, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    # The hidden-jobs list can be large, so it is a separate file the site
    # loads only when the "Hidden jobs" tab is opened.
    hidden_path = out_path.with_name("hidden.json")
    hidden_path.write_text(
        json.dumps(
            {"generated_at": feed.payload["generated_at"], "jobs": feed.hidden},
            ensure_ascii=False,
            separators=(",", ":"),
        ),
        encoding="utf-8",
    )
    logger.info(
        "Wrote web feed with %d eligible jobs to %s (%d hidden student roles in %s)",
        feed.job_count,
        out_path,
        len(feed.hidden),
        hidden_path,
    )
    return out_path
