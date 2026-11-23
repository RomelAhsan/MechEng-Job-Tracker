"""Is each job on the website still open? ("link checker")

Every run, each posting the website would show gets one of three results:

* ``open``: the employer's job board still lists it. Either the board was
  read successfully within :data:`RECENT` (a board fetch lists every posting
  it still has), or the posting's own page/API answered "open".
* ``closed``: the employer's own API says the posting is gone (Workday "not
  found" / "permission denied", Greenhouse/Lever/SmartRecruiters 404, an
  Oracle requisition that no longer exists, a Radancy page that 404s). The
  job is removed from the website immediately.
* ``cant_check``: the job site refused our checker (for example Workday
  answering "too many requests" to GitHub's servers), so we cannot tell. The
  job stays on the website with a "Can't be checked" tag and the reason.

Loading the public job page is NOT used as proof for Workday: its pages are a
JavaScript app that returns HTTP 200 even for a closed posting. The checks
use each job board's own data instead.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

import httpx
from sqlalchemy.orm import Session

from jobtracker.connectors.http import BlockedError, HttpClient
from jobtracker.db.models import Company, Job, JobEvent, SourceHealth
from jobtracker.schemas.job import JobStatus

logger = logging.getLogger("jobtracker.services.link_check")

#: A board read successfully within this window counts as having checked all
#: of its postings.
RECENT = timedelta(hours=3)

OPEN = "open"
CLOSED = "closed"
CANT_CHECK = "cant_check"


@dataclass
class LinkCheck:
    status: str
    reason: str | None = None


@dataclass
class LinkCheckSummary:
    checked: int = 0
    open: int = 0
    closed: list[str] = field(default_factory=list)
    cant_check: int = 0


def _aware(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value if value.tzinfo else value.replace(tzinfo=UTC)


def _cant_check_from(exc: BaseException) -> LinkCheck:
    from jobtracker.services.ingest_service import describe_error

    return LinkCheck(CANT_CHECK, describe_error(exc))


def _closed(reason: str = "the employer took the posting down") -> LinkCheck:
    return LinkCheck(CLOSED, reason)


def _check_workday(http: HttpClient, job: Job, company: Company) -> LinkCheck | None:
    from jobtracker.connectors.workday import parse_workday_url

    url = job.application_url or job.source_url or ""
    if "/job/" not in url:
        return None
    coords = parse_workday_url(company.careers_url or url)
    if coords is None:
        return None
    path = url[url.index("/job/"):]
    response = http.request("GET", f"{coords.cxs_base}{path}")
    if response.status_code in (404, 410):
        return _closed()
    if response.status_code == 403 and "json" in response.headers.get("content-type", ""):
        # Workday's JSON "permission denied" (S22) for an unposted job.
        return _closed()
    if response.status_code != 200:
        return LinkCheck(CANT_CHECK, f"the job site answered HTTP {response.status_code}")
    info = (response.json() or {}).get("jobPostingInfo") or {}
    if info.get("canApply") is False:
        return _closed("the employer is no longer accepting applications")
    return LinkCheck(OPEN)


def _status_check(http: HttpClient, url: str) -> LinkCheck:
    response = http.request("GET", url)
    if response.status_code == 200:
        return LinkCheck(OPEN)
    if response.status_code in (404, 410):
        return _closed()
    return LinkCheck(CANT_CHECK, f"the job site answered HTTP {response.status_code}")


def _check_oracle(http: HttpClient, job: Job, company: Company) -> LinkCheck | None:
    from jobtracker.connectors.oracle import _split_token, parse_oracle_url

    coords = _split_token(company.board_token) or parse_oracle_url(company.careers_url)
    if coords is None or not job.source_job_id:
        return None
    origin, site = coords
    response = http.request(
        "GET",
        f"{origin}/hcmRestApi/resources/latest/recruitingCEJobRequisitionDetails",
        params={
            "onlyData": "true",
            "finder": f'ById;Id="{job.source_job_id}",siteNumber={site}',
        },
    )
    if response.status_code != 200:
        return LinkCheck(CANT_CHECK, f"the job site answered HTTP {response.status_code}")
    items = (response.json() or {}).get("items")
    return LinkCheck(OPEN) if items else _closed()


def _check_radancy(http: HttpClient, job: Job, company: Company) -> LinkCheck | None:
    url = job.application_url or job.source_url
    if not url:
        return None
    response = http.request("GET", url)
    if response.status_code == 200:
        return LinkCheck(OPEN)
    if response.status_code in (404, 410):
        return _closed()
    location = response.headers.get("location", "")
    if response.status_code in (301, 302, 303, 307, 308) and "/job/" not in location:
        # Radancy sends a closed job's page to the search page.
        return _closed()
    return LinkCheck(CANT_CHECK, f"the job site answered HTTP {response.status_code}")


def check_job(http: HttpClient, job: Job, company: Company) -> LinkCheck | None:
    """Ask the employer's job board whether ``job`` is still open.

    Returns ``None`` for boards with no per-posting lookup (Ashby, Workable,
    SuccessFactors, Phenom, feeds); for those only the board fetch can tell.
    """
    token = company.board_token
    sid = job.source_job_id
    try:
        if company.ats == "workday":
            return _check_workday(http, job, company)
        if company.ats == "greenhouse" and token and sid:
            return _status_check(
                http, f"https://boards-api.greenhouse.io/v1/boards/{token}/jobs/{sid}"
            )
        if company.ats == "lever" and token and sid:
            return _status_check(http, f"https://api.lever.co/v0/postings/{token}/{sid}")
        if company.ats == "smartrecruiters" and token and sid:
            return _status_check(
                http, f"https://api.smartrecruiters.com/v1/companies/{token}/postings/{sid}"
            )
        if company.ats == "oracle":
            return _check_oracle(http, job, company)
        if company.ats == "radancy":
            return _check_radancy(http, job, company)
    except BlockedError as exc:
        return LinkCheck(CANT_CHECK, exc.reason)
    except (httpx.HTTPError, ValueError) as exc:
        return _cant_check_from(exc)
    return None


def _remove(session: Session, job: Job, now: datetime) -> None:
    job.status = JobStatus.REMOVED.value
    job.removed_at = now
    session.add(
        JobEvent(
            job_id=job.job_id,
            company_id=job.company_id,
            event_type="removed",
            old_hash=job.description_hash,
            new_hash=None,
            created_at=now,
        )
    )


def run_link_checks(
    session: Session,
    http: HttpClient,
    job_ids: list[str],
    *,
    now: datetime | None = None,
) -> LinkCheckSummary:
    """Check ``job_ids`` (the jobs the website would show) and store results."""
    now = now or datetime.now(UTC)
    summary = LinkCheckSummary()
    health_by_company = {h.company_id: h for h in session.query(SourceHealth).all()}
    for job_id in job_ids:
        job = session.get(Job, job_id)
        if job is None or job.status != JobStatus.ACTIVE.value:
            continue
        company = session.get(Company, job.company_id)
        if company is None:
            continue
        summary.checked += 1
        health = health_by_company.get(company.id)
        last_ok = _aware(health.last_successful_check_at) if health else None
        # A successful read of the board (direct or through Apify) within
        # RECENT listed every posting it still had, so this one is open.
        board_confirmed = last_ok is not None and now - last_ok <= RECENT

        if board_confirmed:
            job.link_status = OPEN
            job.link_checked_at = last_ok
            job.link_check_reason = None
            summary.open += 1
            continue

        result = check_job(http, job, company)
        if result is None:
            from jobtracker.services.ingest_service import plain_reason

            reason = plain_reason(health.last_error if health else None) or (
                "the job board has not been read successfully recently"
            )
            result = LinkCheck(CANT_CHECK, reason)

        if result.status == OPEN:
            job.link_status = OPEN
            job.link_checked_at = now
            job.link_check_reason = None
            summary.open += 1
        elif result.status == CLOSED:
            job.link_status = CLOSED
            job.link_check_reason = result.reason
            _remove(session, job, now)
            summary.closed.append(job.job_id)
            logger.info(
                "Removed %s @ %s: %s", job.title, company.name, result.reason
            )
        else:
            job.link_status = CANT_CHECK
            job.link_check_reason = result.reason
            # Keep the last time it WAS confirmed open (board listing).
            if last_ok and (job.link_checked_at is None or _aware(job.link_checked_at) < last_ok):
                job.link_checked_at = last_ok
            summary.cant_check += 1
    session.flush()
    return summary

