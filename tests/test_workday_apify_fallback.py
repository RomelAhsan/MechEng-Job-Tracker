"""Apify sweep for Workday boards that refuse GitHub's servers."""

from __future__ import annotations

from types import SimpleNamespace

import httpx
from sqlalchemy.orm import Session

from jobtracker.connectors.apify_workday import ApifyWorkdayConnector
from jobtracker.connectors.http import BlockedError, HttpClient, block_reason
from jobtracker.connectors.workday_targeting import TargetingPlan
from jobtracker.db.models import Company, Job, SourceHealth
from jobtracker.schemas.company import CompanyConfig
from jobtracker.services import ingest_service
from jobtracker.services.apify_budget import ApifyBudget
from jobtracker.services.ingest_service import _is_blocked_error
from jobtracker.settings import Settings

BASE = "https://ge.wd5.myworkdayjobs.com"


class _FakeRun:
    def __init__(self, status: str, dataset: str) -> None:
        self.status = status
        self.dataset = dataset
        self.aborted = False

    def wait_for_finish(self, *, wait_duration=None):
        return SimpleNamespace(
            status=self.status, usage_total_usd=0.002, default_dataset_id=self.dataset
        )

    def abort(self):
        self.aborted = True

    def get(self):
        return SimpleNamespace(status="ABORTED", usage_total_usd=0.001)


class _FakeClient:
    """Listing run returns two postings; details run returns the new one."""

    def __init__(self, status: str = "SUCCEEDED") -> None:
        self.started: list[dict] = []
        self.status = status
        self.runs: dict[str, _FakeRun] = {}

    def actor(self, actor_id):
        client = self

        class _A:
            def start(self, *, run_input, **kwargs):
                client.started.append(run_input)
                run_id = f"run-{len(client.started)}"
                dataset = "details" if run_input["details"] else "listing"
                client.runs[run_id] = _FakeRun(client.status, dataset)
                return SimpleNamespace(id=run_id)

        return _A()

    def run(self, run_id):
        return self.runs[run_id]

    def dataset(self, dataset_id):
        if dataset_id == "listing":
            items = [
                {"title": "Mechanical Intern", "externalPath": "/job/Markham/Mech_R-1",
                 "url": f"{BASE}/Site/job/Markham/Mech_R-1", "postedOn": "Posted Today"},
                {"title": "Thermal Co-op", "externalPath": "/job/Markham/Thermal_R-2",
                 "url": f"{BASE}/Site/job/Markham/Thermal_R-2", "postedOn": "Posted 3 Days Ago"},
            ]
        else:
            items = [
                {"id": "wd-2", "title": "Thermal Co-op", "jobDescription": "<p>Thermal</p>",
                 "location": "Markham", "country": "Canada", "startDate": "2026-11-20",
                 "externalUrl": f"{BASE}/Site/job/Markham/Thermal_R-2",
                 "url": f"{BASE}/Site/job/Markham/Thermal_R-2"},
            ]
        return SimpleNamespace(list_items=lambda: SimpleNamespace(items=items))


def _connector(tmp_path, client) -> ApifyWorkdayConnector:
    settings = Settings(apify_token="tok", user_file="config/user.yaml")
    budget = ApifyBudget(monthly_budget_usd=5.0, ledger_path=tmp_path / "l.json")
    return ApifyWorkdayConnector(settings=settings, budget=budget, client=client)


def test_search_urls_use_cached_filters_or_precise_keywords() -> None:
    company = CompanyConfig(id="ge", name="GE", ats="workday", careers_url=f"{BASE}/Site")
    plan = TargetingPlan(
        full_board=False,
        country={"locationCountry": ["ca", "us"]},
        student={"workerSubType": ["stu"]},
        keywords=["internship"],
    )
    urls = ApifyWorkdayConnector.search_urls(company, plan)
    assert urls[0] == (
        f"{BASE}/en-US/Site?workerSubType=stu&locationCountry=ca&locationCountry=us"
    )
    assert urls[1] == f"{BASE}/en-US/Site?q=internship&locationCountry=ca&locationCountry=us"
    fallback = ApifyWorkdayConnector.search_urls(company, None)
    assert f"{BASE}/en-US/Site?q=internship" in fallback
    assert not any("q=intern&" in u or u.endswith("q=intern") for u in fallback)


def test_unfinished_run_is_aborted_and_usage_recorded(tmp_path) -> None:
    client = _FakeClient(status="RUNNING")
    conn = _connector(tmp_path, client)
    run_id = conn.start_listing([f"{BASE}/en-US/Site?q=internship"])
    assert conn.wait_items(run_id, wait_secs=1) is None
    assert client.runs[run_id].aborted is True
    assert conn._budget.check().spent_this_month_usd > 0


def test_sweep_reuses_stored_postings_and_fetches_only_new(
    session: Session, tmp_path, monkeypatch
) -> None:
    session.add(
        Company(id="ge", name="GE", ats="workday", enabled=True, active=True,
                careers_url=f"{BASE}/Site")
    )
    session.add(
        Job(job_id="j1", company_id="ge", source_job_id="wd-1", title="Mechanical Intern",
            description="Design parts.", status="active", location="Markham, Canada",
            application_url=f"{BASE}/en-US/Site/job/Markham/Mech_R-1")
    )
    session.add(
        Job(job_id="j0", company_id="ge", source_job_id="wd-0", title="Closed Intern",
            description="Gone.", status="active", location="Markham, Canada",
            application_url=f"{BASE}/en-US/Site/job/Markham/Closed_R-0")
    )
    session.flush()

    client = _FakeClient()
    connector = _connector(tmp_path, client)
    fallback = ingest_service._WorkdayFallback()
    fallback._connector = connector
    fallback._plans = {}
    company = session.get(Company, "ge")
    request = httpx.Request("POST", f"{BASE}/wday/cxs/ge/Site/jobs")
    blocked = BlockedError(
        "429", request=request, response=httpx.Response(429, request=request), reason="x"
    )
    assert fallback.note_blocked(company, blocked)
    fallback.start()
    results = list(fallback.collect(session))

    assert len(client.started) == 2  # one listing run, one details run
    assert client.started[1]["startUrls"] == [
        {"url": f"{BASE}/en-US/Site/job/Markham/Thermal_R-2"}
    ]
    (got_company, raws), = results
    assert got_company.id == "ge"
    by_id = {r.source_job_id: r for r in raws}
    assert by_id["wd-1"].description_html == "Design parts."  # reused, no download
    assert by_id["wd-2"].location == "Markham, Canada"  # new, from details run

    ingest_service._process_raw_jobs(
        session, company, ingest_service._config_from_company(company), raws, via="apify"
    )
    assert session.get(Job, "j0").status == "removed"  # missing from listing: closed
    assert session.get(Job, "j1").status == "active"
    assert session.get(SourceHealth, "ge").last_fetch_via == "apify"
    # A second sweep right away is not due yet (protects the free credit).
    assert not connector.due("ge")


def test_blocked_error_detection() -> None:
    request = httpx.Request("POST", "https://x.wd1.myworkdayjobs.com/wday/cxs/x/y/jobs")
    legacy = httpx.HTTPStatusError(
        "521", request=request, response=httpx.Response(521, request=request)
    )
    assert _is_blocked_error(legacy)
    assert not _is_blocked_error(ValueError("x"))


def test_block_reason_ignores_json_answers() -> None:
    request = httpx.Request("GET", "https://ag.wd3.myworkdayjobs.com/wday/cxs/ag/A/job/x")
    html_429 = httpx.Response(
        429, request=request, headers={"content-type": "text/html", "server": "cloudflare"}
    )
    json_403 = httpx.Response(
        403, request=request, headers={"content-type": "application/json"},
        json={"errorCode": "S22", "message": "permission denied"},
    )
    assert "too many requests" in (block_reason(html_429) or "")
    assert block_reason(json_403) is None


def test_http_client_stops_asking_a_site_that_refuses_us() -> None:
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.host)
        return httpx.Response(429, headers={"content-type": "text/html"}, text="<html>")

    client = httpx.Client(transport=httpx.MockTransport(handler))
    http = HttpClient(Settings(rate_limit_per_host=0), client=client, sleep=lambda s: None)
    for tenant in ("a", "b", "c", "d"):
        try:
            http.request("POST", f"https://{tenant}.wd3.myworkdayjobs.com/wday/cxs/x/y/jobs")
        except BlockedError:
            pass
    # Two hosts refused us (no retries each); the other two were not asked.
    assert calls == ["a.wd3.myworkdayjobs.com", "b.wd3.myworkdayjobs.com"]


def test_force_sweep_ignores_recent_sweeps(session: Session, tmp_path, monkeypatch) -> None:
    session.add(
        Company(id="ge", name="GE", ats="workday", enabled=True, active=True,
                careers_url=f"{BASE}/Site")
    )
    session.flush()
    client = _FakeClient()
    connector = _connector(tmp_path, client)
    connector.mark_fetched(["ge"])  # swept moments ago
    request = httpx.Request("POST", f"{BASE}/wday/cxs/ge/Site/jobs")
    blocked = BlockedError(
        "429", request=request, response=httpx.Response(429, request=request), reason="x"
    )

    def sweep(force: bool) -> int:
        settings = Settings(user_file="config/user.yaml", apify_force_sweep=force)
        monkeypatch.setattr(ingest_service, "get_settings", lambda: settings)
        fallback = ingest_service._WorkdayFallback()
        fallback._connector = connector
        fallback._plans = {}
        fallback.note_blocked(session.get(Company, "ge"), blocked)
        before = len(client.started)
        fallback.start()
        return len(client.started) - before

    assert sweep(False) == 0
    assert sweep(True) == 1


def test_committed_sweep_request_makes_boards_due(tmp_path) -> None:
    import json as _json
    import time as _time

    from jobtracker.connectors.workday_targeting import sweep_requested_at

    client = _FakeClient()
    connector = _connector(tmp_path, client)
    connector.mark_fetched(["ge"])
    path = tmp_path / "filters.json"
    path.write_text(_json.dumps({"boards": {}, "sweep_requested_at": "2999-01-01T00:00:00+00:00"}))
    requested = sweep_requested_at(path)
    assert requested is not None and requested > _time.time()
    assert connector.swept_before("ge", requested)  # swept before the request
    assert not connector.swept_before("ge", 0.0)
