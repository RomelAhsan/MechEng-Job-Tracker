"""Tests for the static web feed (always-on React site data source).

The feed must contain exactly the eligible "Relevant For Me" set -- i.e. it
must inherit the Stage-1 hard filter and the Stage-2 AI gate from
``dashboard_service.list_relevant_jobs`` rather than re-deriving eligibility --
and it must always be a well-formed file, even when empty.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

from sqlalchemy.orm import Session

from jobtracker.ai.prompts import PROMPT_VERSION
from jobtracker.db.models import Company, Job
from jobtracker.services import web_export_service


def _company(session: Session) -> None:
    session.add(Company(id="acme", name="Acme Robotics", ats="greenhouse", active=True))


def _job(session: Session, job_id: str, title: str, *, eligible: bool | None) -> None:
    session.add(
        Job(
            job_id=job_id,
            company_id="acme",
            source_job_id=job_id,
            title=title,
            location="Toronto, Ontario, Canada",
            term_season="spring",
            duration_months=4,
            duration_label="4-month",
            fit_score=90,
            application_url=f"https://apply.example/{job_id}",
            source_url=f"https://src.example/{job_id}",
            description="Design mechanical systems.",
            status="active",
            classification="strong_match",
            eligible=eligible,
        )
    )


def _dated_job(
    session: Session,
    job_id: str,
    *,
    posted_at: datetime | None = None,
    first_seen_at: datetime | None = None,
    term_season: str = "unknown",
    term_start_date: datetime | None = None,
) -> None:
    session.add(
        Job(
            job_id=job_id,
            company_id="acme",
            source_job_id=job_id,
            title="Mechanical Engineering Intern",
            location="Toronto, Ontario, Canada",
            term_season=term_season,
            term_start_date=term_start_date,
            duration_months=4,
            duration_label="4-month",
            fit_score=90,
            application_url=f"https://apply.example/{job_id}",
            source_url=f"https://src.example/{job_id}",
            description="Design mechanical systems.",
            status="active",
            classification="strong_match",
            eligible=True,
            posted_at=posted_at,
            first_seen_at=first_seen_at,
        )
    )


def test_freshness_cutoff_hides_old_jobs_and_sets_label(session: Session) -> None:
    _company(session)
    now = datetime.now(UTC)
    # Fresh by true posting date (10 days ago) -> kept, label "10".
    _dated_job(session, "fresh", posted_at=now - timedelta(days=10))
    # Stale by true posting date (20 days ago) -> hidden.
    _dated_job(session, "stale", posted_at=now - timedelta(days=20))
    # No posted_at, but first seen 5 days ago -> fresh, kept.
    _dated_job(session, "seen-fresh", first_seen_at=now - timedelta(days=5))
    # No posted_at, first seen 30 days ago -> stale, hidden.
    _dated_job(session, "seen-stale", first_seen_at=now - timedelta(days=30))
    # No dates at all -> age indeterminate -> KEPT.
    _dated_job(session, "unknown")
    session.flush()

    feed = web_export_service.build_feed(session)
    by_id = {j["job_id"]: j for j in feed.payload["jobs"]}

    assert "fresh" in by_id
    assert "seen-fresh" in by_id
    assert "unknown" in by_id
    assert "stale" not in by_id
    assert "seen-stale" not in by_id

    assert by_id["fresh"]["posted_days_ago"] == 10
    assert by_id["seen-fresh"]["posted_days_ago"] == 5
    assert by_id["unknown"]["posted_days_ago"] is None


def test_freshness_boundary_at_14_days(session: Session) -> None:
    _company(session)
    now = datetime.now(UTC)
    # Exactly 14 days ago is still within the window (<= 14) -> kept.
    _dated_job(session, "boundary", posted_at=now - timedelta(days=14, hours=-1))
    # Just past 14 days -> hidden.
    _dated_job(session, "past", posted_at=now - timedelta(days=14, hours=1))
    session.flush()

    ids = {j["job_id"] for j in web_export_service.build_feed(session).payload["jobs"]}
    assert "boundary" in ids
    assert "past" not in ids


def test_posted_days_ago_lower_bound_for_workday_30_plus() -> None:
    # Workday's "Posted 30+ Days Ago" resolves to now-30d (so the freshness
    # cutoff still hides it). The feed must flag that age as a LOWER BOUND so
    # the website renders "Posted 30+ days ago" rather than a precise but wrong
    # "Posted 30 days ago". Exercised via a minimal view: the helper only reads
    # posted_at / first_seen_at.
    now = datetime(2026, 2, 1, 12, 0, tzinfo=UTC)
    thirty_plus = SimpleNamespace(
        posted_at=now - timedelta(days=30), first_seen_at=None
    )
    exact_ten = SimpleNamespace(posted_at=now - timedelta(days=10), first_seen_at=None)
    seen_only = SimpleNamespace(
        posted_at=None, first_seen_at=now - timedelta(days=40)
    )

    assert web_export_service.posted_days_ago(thirty_plus, now=now) == 30
    assert web_export_service.posted_days_ago_is_lower_bound(thirty_plus, now=now) is True
    # An exact, recent posting date is never a lower bound.
    assert web_export_service.posted_days_ago_is_lower_bound(exact_ten, now=now) is False
    # Age derived from first_seen_at (no source posting date) is exact, never
    # a lower bound, even when old.
    assert web_export_service.posted_days_ago_is_lower_bound(seen_only, now=now) is False


def test_posted_days_ago_prefers_true_posting_date(session: Session) -> None:
    _company(session)
    now = datetime.now(UTC)
    # True posting 3 days ago; first seen only 1 day ago (we fetched it late).
    _dated_job(
        session,
        "job",
        posted_at=now - timedelta(days=3),
        first_seen_at=now - timedelta(days=1),
    )
    session.flush()
    job = web_export_service.build_feed(session).payload["jobs"][0]
    # The label reflects the TRUE posting age, not the fetch age.
    assert job["posted_days_ago"] == 3


def test_tracked_count_excludes_stub_companies(session: Session) -> None:
    # Only connector-backed, enabled, active companies count as "tracked".
    session.add(Company(id="acme", name="Acme", ats="greenhouse", active=True, enabled=True))
    session.add(Company(id="stub", name="Stub Co", ats="html", active=True, enabled=True))
    session.add(Company(id="off", name="Off", ats="lever", active=True, enabled=False))
    session.flush()
    feed = web_export_service.build_feed(session)
    # greenhouse counts; html stub and disabled lever do not.
    assert feed.payload["tracked_company_count"] == 1


def _active_job(session: Session, job_id: str, company_id: str) -> None:
    session.add(
        Job(
            job_id=job_id,
            company_id=company_id,
            source_job_id=job_id,
            title="Mechanical Engineering Intern",
            status="active",
        )
    )


def test_tracked_companies_list_matches_count_and_shape(session: Session) -> None:
    # Same predicate as the count: active + enabled + non-stub ATS. Two eligible
    # companies (added out of alphabetical order), plus a html stub and a
    # disabled company that must both be excluded.
    session.add(
        Company(
            id="zeta",
            name="Zeta Dynamics",
            ats="lever",
            active=True,
            enabled=True,
            careers_url="https://jobs.lever.co/zeta",
        )
    )
    session.add(
        Company(
            id="acme",
            name="Acme Robotics",
            ats="greenhouse",
            active=True,
            enabled=True,
            careers_url="https://www.careers.acme.com/jobs",
        )
    )
    session.add(Company(id="stub", name="Stub Co", ats="html", active=True, enabled=True))
    session.add(Company(id="off", name="Off Co", ats="lever", active=True, enabled=False))
    # Two active jobs for Acme, one for Zeta; a non-active job must not count.
    _active_job(session, "acme-1", "acme")
    _active_job(session, "acme-2", "acme")
    _active_job(session, "zeta-1", "zeta")
    session.add(
        Job(job_id="acme-old", company_id="acme", source_job_id="acme-old", status="removed")
    )
    session.flush()

    payload = web_export_service.build_feed(session).payload
    tracked = payload["tracked_companies"]

    # The html stub and the disabled company are excluded.
    names = [c["name"] for c in tracked]
    assert names == ["Acme Robotics", "Zeta Dynamics"]  # sorted by name
    assert "Stub Co" not in names
    assert "Off Co" not in names

    # Each item carries name/ats/job_count.
    by_name = {c["name"]: c for c in tracked}
    assert by_name["Acme Robotics"]["ats"] == "greenhouse"
    assert by_name["Acme Robotics"]["job_count"] == 2  # only active jobs
    # The careers host is derived from careers_url (leading "www." stripped) so
    # the Add tab can block a pasted careers URL for an already-tracked company.
    assert by_name["Acme Robotics"]["host"] == "careers.acme.com"
    assert by_name["Zeta Dynamics"]["ats"] == "lever"
    assert by_name["Zeta Dynamics"]["job_count"] == 1
    assert by_name["Zeta Dynamics"]["host"] == "jobs.lever.co"

    # The list and the count never disagree.
    assert len(tracked) == payload["tracked_company_count"] == 2


def test_job_without_ai_analysis_has_null_reasoning(session: Session) -> None:
    _company(session)
    _job(session, "keep-1", "Mechanical Engineering Intern", eligible=True)
    session.flush()
    job = web_export_service.build_feed(session).payload["jobs"][0]
    assert job["ai_reasoning"] is None


def test_job_with_ai_analysis_exposes_reasoning(session: Session) -> None:
    from jobtracker.db.models import AiAnalysisCache

    _company(session)
    _job(session, "keep-1", "Mechanical Engineering Intern", eligible=True)
    # Cache an AI analysis for the current (job, description_hash, version) key.
    job = session.get(Job, "keep-1")
    session.add(
        AiAnalysisCache(
            job_id="keep-1",
            description_hash=job.description_hash,
            profile_version=1,
            result={
                "recommendation": "apply",
                "relevance_score": 88,
                "confidence": 0.9,
                "explanation": "Strong mechanical co-op fit.",
                "work_authorization_assessment": "Canada-located; eligible.",
                "concerns": [],
                "matched_skills": ["CAD"],
                "_prompt_version": PROMPT_VERSION,
            },
        )
    )
    session.flush()
    job_dict = web_export_service.build_feed(session).payload["jobs"][0]
    reasoning = job_dict["ai_reasoning"]
    assert reasoning is not None
    assert reasoning["recommendation"] == "apply"
    assert reasoning["explanation"] == "Strong mechanical co-op fit."
    assert "CAD" in reasoning["matched_skills"]


def test_feed_has_wellformed_shape_when_empty(session: Session) -> None:
    feed = web_export_service.build_feed(session)
    payload = feed.payload
    assert payload["version"] == web_export_service.FEED_VERSION
    assert payload["job_count"] == 0
    assert payload["jobs"] == []
    assert "generated_at" in payload  # always stamped


def test_feed_contains_only_eligible_relevant_jobs(session: Session) -> None:
    _company(session)
    # Stage-1 survivor (eligible True) + unscreened (None, kept) + hard-dropped.
    _job(session, "keep-1", "Mechanical Engineering Intern", eligible=True)
    _job(session, "keep-2", "Mechanical Design Co-op", eligible=None)
    _job(session, "drop-1", "Senior Mechanical Engineer", eligible=False)
    session.flush()

    feed = web_export_service.build_feed(session)
    ids = {j["job_id"] for j in feed.payload["jobs"]}

    # The hard-dropped job must never reach the website.
    assert "drop-1" not in ids
    assert {"keep-1", "keep-2"} <= ids
    assert feed.payload["job_count"] == len(feed.payload["jobs"])


def test_feed_job_carries_display_fields(session: Session) -> None:
    _company(session)
    _job(session, "keep-1", "Mechanical Engineering Intern", eligible=True)
    session.flush()

    job = web_export_service.build_feed(session).payload["jobs"][0]
    for field in (
        "title",
        "company",
        "location",
        "fit_score",
        "application_url",
        "source_url",
        "term_season",
        "duration_label",
    ):
        assert field in job
    assert job["company"] == "Acme Robotics"
    assert job["application_url"].endswith("keep-1")


def test_export_web_writes_valid_json_file(session: Session, tmp_path) -> None:
    _company(session)
    _job(session, "keep-1", "Mechanical Engineering Intern", eligible=True)
    session.flush()

    out = tmp_path / "data" / "jobs.json"
    path = web_export_service.export_web(session, output=str(out))

    assert path.exists()
    parsed = json.loads(path.read_text(encoding="utf-8"))
    assert parsed["job_count"] == 1
    assert parsed["jobs"][0]["job_id"] == "keep-1"


def test_export_web_writes_empty_feed_without_crashing(session: Session, tmp_path) -> None:
    out = tmp_path / "data" / "jobs.json"
    path = web_export_service.export_web(session, output=str(out))
    parsed = json.loads(path.read_text(encoding="utf-8"))
    assert parsed["jobs"] == []


def test_upcoming_term_is_never_hidden_by_age(session: Session) -> None:
    """Summer internships are posted months ahead; age alone must not hide them."""
    _company(session)
    now = datetime.now(UTC)
    old = now - timedelta(days=60)
    _dated_job(session, "summer-2027", posted_at=old, term_season="spring",
               term_start_date=now + timedelta(days=150))
    _dated_job(session, "no-year", posted_at=old, term_season="winter")
    _dated_job(session, "started", posted_at=old, term_season="fall",
               term_start_date=now - timedelta(days=30))
    _dated_job(session, "no-term", posted_at=old)
    session.flush()

    ids = {j["job_id"] for j in web_export_service.build_feed(session).payload["jobs"]}
    assert {"summer-2027", "no-year"} <= ids
    assert "started" not in ids  # term already under way and posted 60 days ago
    assert "no-term" not in ids  # no term stated: the 14-day rule applies


def test_card_fields_for_date_source_and_link_check(session: Session) -> None:
    _company(session)
    now = datetime.now(UTC)
    _dated_job(session, "dated", posted_at=now - timedelta(days=3))
    _dated_job(session, "undated", first_seen_at=now - timedelta(days=2))
    session.flush()
    blocked = session.get(Job, "undated")
    blocked.link_status = "cant_check"
    blocked.link_check_reason = "the job site is refusing our checker"
    blocked.link_checked_at = now - timedelta(days=5)
    session.flush()

    jobs = {j["job_id"]: j for j in web_export_service.build_feed(session).payload["jobs"]}
    assert jobs["dated"]["posted_date_source"] == "employer"
    assert jobs["undated"]["posted_date_source"] == "first_seen"
    assert jobs["undated"]["link_status"] == "cant_check"  # tagged, not hidden
    assert jobs["undated"]["cant_check_reason"] == "the job site is refusing our checker"
    assert jobs["dated"]["cant_check_reason"] is None


def test_reported_jobs_are_excluded(session: Session, tmp_path, monkeypatch) -> None:
    import json as _json

    _company(session)
    _dated_job(session, "dead", first_seen_at=datetime.now(UTC))
    _dated_job(session, "alive", first_seen_at=datetime.now(UTC))
    session.flush()
    reported = tmp_path / "reported.json"
    reported.write_text(_json.dumps({"jobs": [{"job_id": "dead"}]}))
    monkeypatch.setattr(web_export_service, "REPORTED_FILE", str(reported))
    monkeypatch.setattr(
        web_export_service, "load_reported",
        lambda path=str(reported): {"dead": {"job_id": "dead"}},
    )
    feed = web_export_service.build_feed(session)
    ids = {j["job_id"] for j in feed.payload["jobs"]}
    assert ids == {"alive"}
    assert [h["group"] for h in feed.hidden if h["job_id"] == "dead"] == ["reported"]
