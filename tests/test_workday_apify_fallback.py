"""Apify fallback for Workday boards that answer 521 to the CI runner."""

from __future__ import annotations

from types import SimpleNamespace

import httpx

from jobtracker.connectors.apify_workday import ApifyWorkdayConnector
from jobtracker.schemas.company import CompanyConfig
from jobtracker.services.apify_budget import ApifyBudget
from jobtracker.services.ingest_service import _is_blocked_error
from jobtracker.settings import Settings


class _FakeRun:
    def __init__(self, status: str) -> None:
        self.status = status
        self.aborted = False

    def wait_for_finish(self, *, wait_duration=None):
        return SimpleNamespace(
            status=self.status, usage_total_usd=0.02, default_dataset_id="ds1"
        )

    def abort(self):
        self.aborted = True

    def get(self):
        return SimpleNamespace(status="ABORTED", usage_total_usd=0.01)


class _FakeClient:
    def __init__(self, status: str = "SUCCEEDED") -> None:
        self.started: list[dict] = []
        self.run_obj = _FakeRun(status)

    def actor(self, actor_id):
        client = self

        class _A:
            def start(self, *, run_input, **kwargs):
                client.started.append(run_input)
                return SimpleNamespace(id="run-1")

        return _A()

    def run(self, run_id):
        return self.run_obj

    def dataset(self, dataset_id):
        items = [
            {"id": "R-1", "title": "Mechanical Intern", "location": "Markham",
             "url": "https://ge.wd5.myworkdayjobs.com/Site/job/Markham/Mech_R-1"},
            {"id": "R-1", "title": "Mechanical Intern", "location": "Markham",
             "url": "https://ge.wd5.myworkdayjobs.com/Site/job/Markham/Mech_R-1"},
        ]
        return SimpleNamespace(list_items=lambda: SimpleNamespace(items=items))


def _company() -> CompanyConfig:
    return CompanyConfig(
        id="ge", name="GE", ats="workday",
        careers_url="https://ge.wd5.myworkdayjobs.com/Site",
    )


def _connector(tmp_path, client) -> ApifyWorkdayConnector:
    settings = Settings(apify_token="tok", user_file="config/user.yaml")
    budget = ApifyBudget(monthly_budget_usd=5.0, ledger_path=tmp_path / "l.json")
    return ApifyWorkdayConnector(settings=settings, budget=budget, client=client)


def test_start_searches_student_roles_and_spaces_runs(tmp_path) -> None:
    client = _FakeClient()
    conn = _connector(tmp_path, client)
    assert conn.start_student_search(_company()) == "run-1"
    urls = [u["url"] for u in client.started[0]["startUrls"]]
    assert urls[0] == "https://ge.wd5.myworkdayjobs.com/en-US/Site?q=intern"
    assert len(urls) == 3
    # Second attempt within the interval is skipped (protects free credit).
    assert conn.start_student_search(_company()) is None


def test_collect_dedupes_and_records_usage(tmp_path) -> None:
    client = _FakeClient()
    conn = _connector(tmp_path, client)
    jobs = conn.collect("run-1", wait_secs=5)
    assert jobs is not None and len(jobs) == 1
    assert "/en-US/Site/job/" in (jobs[0].application_url or "")
    assert conn._budget.check().spent_this_month_usd > 0


def test_unfinished_run_is_aborted(tmp_path) -> None:
    client = _FakeClient(status="RUNNING")
    conn = _connector(tmp_path, client)
    assert conn.collect("run-1", wait_secs=1) is None
    assert client.run_obj.aborted is True


def test_blocked_error_detection() -> None:
    request = httpx.Request("POST", "https://x.wd1.myworkdayjobs.com/wday/cxs/x/y/jobs")
    err = httpx.HTTPStatusError(
        "521", request=request, response=httpx.Response(521, request=request)
    )
    assert _is_blocked_error(err)
    assert not _is_blocked_error(ValueError("x"))


def test_actor_api_url_becomes_public_link() -> None:
    raw = ApifyWorkdayConnector._to_raw(
        {
            "id": "",
            "title": "Mechanical Intern",
            "locationText": "Dallas",
            "externalPath": "/job/Dallas/Mechanical-Intern_123",
            "url": "https://cae.wd3.myworkdayjobs.com/wday/cxs/cae/career/job/Dallas/Mechanical-Intern_123",
        }
    )
    assert raw is not None
    assert raw.location == "Dallas"
    assert raw.application_url == (
        "https://cae.wd3.myworkdayjobs.com/career/job/Dallas/Mechanical-Intern_123"
    )
