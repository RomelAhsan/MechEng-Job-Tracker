"""Link checker, coverage panel data, hidden-jobs list, and rule audit."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import httpx
import respx
from sqlalchemy.orm import Session

from jobtracker.ai.provider import AIProvider, AIResult
from jobtracker.connectors.http import HttpClient
from jobtracker.db.models import Company, Job, SourceHealth
from jobtracker.services import audit_service, link_check_service, web_export_service
from jobtracker.settings import Settings

WD = "https://ag.wd3.myworkdayjobs.com"
PATH = "/job/Montreal/Stagiaire_JR1"
DETAIL = f"{WD}/wday/cxs/ag/Airbus{PATH}"


def _http() -> HttpClient:
    return HttpClient(Settings(rate_limit_per_host=0, http_max_retries=0))


def _seed(session: Session, *, last_ok: datetime | None, status: str = "error") -> Job:
    session.add(
        Company(id="airbus", name="Airbus", ats="workday", enabled=True, active=True,
                careers_url=f"{WD}/Airbus")
    )
    session.add(
        SourceHealth(company_id="airbus", last_status=status,
                     last_checked_at=datetime.now(UTC), last_successful_check_at=last_ok,
                     last_error="the job site is refusing our checker (too many requests "
                     "from GitHub's servers)")
    )
    job = Job(job_id="j1", company_id="airbus", source_job_id="wd-1",
              title="Stagiaire en Outillages", status="active",
              application_url=f"{WD}/en-US/Airbus{PATH}")
    session.add(job)
    session.flush()
    return job


def test_recently_read_board_counts_as_checked(session: Session) -> None:
    job = _seed(session, last_ok=datetime.now(UTC) - timedelta(minutes=40), status="ok")
    with respx.mock(assert_all_called=False) as mock:
        route = mock.get(DETAIL)
        summary = link_check_service.run_link_checks(session, _http(), ["j1"])
        assert not route.called  # no extra request needed
    assert summary.open == 1
    assert job.link_status == "open"


@respx.mock
def test_closed_workday_posting_is_removed(session: Session) -> None:
    job = _seed(session, last_ok=datetime.now(UTC) - timedelta(days=7))
    respx.get(DETAIL).mock(
        return_value=httpx.Response(
            403, json={"errorCode": "S22", "message": "permission denied"}
        )
    )
    summary = link_check_service.run_link_checks(session, _http(), ["j1"])
    assert summary.closed == ["j1"]
    assert job.status == "removed"
    assert job.link_status == "closed"


@respx.mock
def test_blocked_board_is_tagged_not_hidden(session: Session) -> None:
    week_ago = datetime.now(UTC) - timedelta(days=7)
    job = _seed(session, last_ok=week_ago)
    respx.get(DETAIL).mock(
        return_value=httpx.Response(
            429, headers={"content-type": "text/html", "server": "cloudflare"}, text="<html>"
        )
    )
    summary = link_check_service.run_link_checks(session, _http(), ["j1"])
    assert summary.cant_check == 1
    assert job.status == "active"
    assert job.link_status == "cant_check"
    assert "too many requests" in (job.link_check_reason or "")
    assert job.link_checked_at is not None  # last time it was confirmed open


@respx.mock
def test_open_workday_posting(session: Session) -> None:
    job = _seed(session, last_ok=None)
    respx.get(DETAIL).mock(
        return_value=httpx.Response(200, json={"jobPostingInfo": {"canApply": True}})
    )
    link_check_service.run_link_checks(session, _http(), ["j1"])
    assert job.link_status == "open"
    assert job.status == "active"


def _rule_hidden(session: Session) -> None:
    session.add(Company(id="acme", name="Acme", ats="greenhouse", enabled=True, active=True))
    session.add(Company(id="stub", name="Stub Co", ats="html", enabled=True, active=True))
    session.add(
        Job(job_id="h1", company_id="acme", source_job_id="1", title="Thermal Intern",
            status="active", classification="strong_match", eligible=False,
            country="Canada", eligibility_failed_rules=["graduation_window"],
            eligibility_reasons=["Requires graduating before 2031."],
            eligibility_evidence=["Expected graduation between Fall 2027 and Summer 2028"])
    )
    session.add(
        Job(job_id="h2", company_id="acme", source_job_id="2", title="Senior Engineer",
            status="active", classification="strong_match", eligible=False,
            eligibility_failed_rules=["not_student_role"])
    )
    session.flush()


def test_hidden_jobs_and_coverage(session: Session) -> None:
    _rule_hidden(session)
    feed = web_export_service.build_feed(session)
    hidden = {h["job_id"]: h for h in feed.hidden}
    assert set(hidden) == {"h1"}  # regular jobs are not listed as hidden
    assert hidden["h1"]["group"] == "graduation_window"
    assert hidden["h1"]["reasons"][0]["detail"] == "Requires graduating before 2031."
    coverage = {c["id"]: c for c in feed.payload["coverage"]}
    assert coverage["stub"]["status"] == "not_supported"
    assert coverage["acme"]["hidden"] == 1


def test_failing_board_raises_a_warning(session: Session) -> None:
    _seed(session, last_ok=datetime.now(UTC) - timedelta(days=7))
    feed = web_export_service.build_feed(session)
    (warning,) = feed.payload["warnings"]
    assert warning["count"] == 1 and "Airbus" in warning["message"]
    airbus = next(c for c in feed.payload["coverage"] if c["id"] == "airbus")
    assert airbus["status"] == "failing"
    assert "too many requests" in airbus["error"]


class _AuditProvider(AIProvider):
    name = "fake"
    available = True

    def analyze(self, prompt: str) -> AIResult:
        item = {
            "index": 0, "relevance_score": 80, "eligible": True, "failed_rules": [],
            "recommendation": "apply", "mechanical_relevance": "x", "term_assessment": "x",
            "duration_assessment": "x", "location_assessment": "x",
            "work_authorization_assessment": "x", "matched_skills": [], "missing_skills": [],
            "concerns": [], "evidence": [], "explanation": "Grad date is a preference.",
            "confidence": 0.8,
        }
        return AIResult(text=json.dumps([item]), available=True, provider="fake")


def test_rule_audit_flags_possible_mistakes(session: Session) -> None:
    _rule_hidden(session)
    settings = Settings(user_file="config/user.yaml", gemini_api_key="k")
    done = audit_service.audit_rejected_jobs(
        session, limit=5, settings=settings, provider=_AuditProvider()
    )
    assert done == 1
    job = session.get(Job, "h1")
    assert job.rule_audit["ai_eligible"] is True
    assert job.eligible is False  # the rule decision is unchanged
    hidden = {h["job_id"]: h for h in web_export_service.build_feed(session).hidden}
    assert hidden["h1"]["ai_audit"]["ai_eligible"] is True
    # Already audited under the same rules: not picked again.
    assert audit_service.audit_candidates(session, limit=5) == []
