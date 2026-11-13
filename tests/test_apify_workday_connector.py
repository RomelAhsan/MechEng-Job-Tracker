"""Tests for the Apify Workday connector (key-ready, fail-soft, no network)."""

from __future__ import annotations

from jobtracker.connectors.apify_workday import ApifyWorkdayConnector
from jobtracker.schemas.company import ATSType, CompanyConfig
from jobtracker.services.apify_budget import ApifyBudget
from jobtracker.settings import Settings


def _company() -> CompanyConfig:
    return CompanyConfig(
        id="wsp",
        name="WSP",
        ats=ATSType.APIFY_WORKDAY,
        careers_url="https://wsp.wd3.myworkdayjobs.com/WSP_Careers",
    )


class _FakeDataset:
    def __init__(self, items: list[dict]) -> None:
        self._items = items

    def list_items(self):  # noqa: ANN201 - mimics apify-client shape
        class _R:
            items = self._items

        return _R()


class _FakeActor:
    def __init__(self, run: dict) -> None:
        self._run = run

    def call(self, run_input=None, **kwargs):  # noqa: ANN001, ANN003
        self.last_input = run_input
        self.last_opts = kwargs
        return self._run


class _FakeClient:
    def __init__(self, run: dict, items: list[dict]) -> None:
        self._actor = _FakeActor(run)
        self._items = items

    def actor(self, _id):  # noqa: ANN001
        return self._actor

    def dataset(self, _id):  # noqa: ANN001
        return _FakeDataset(self._items)


def test_no_token_yields_no_jobs_and_never_raises(tmp_path) -> None:
    conn = ApifyWorkdayConnector(
        settings=Settings(apify_token=None),
        budget=ApifyBudget(monthly_budget_usd=5.0, ledger_path=tmp_path / "u.json"),
    )
    assert conn.available is False
    assert conn.fetch(_company()) == []


def test_fetch_maps_dataset_items_to_rawjobs(tmp_path) -> None:
    run = {"defaultDatasetId": "ds1", "usageTotalUsd": 0.0}
    items = [
        {
            "id": "R-123",
            "title": "Mechanical Engineering Intern",
            "location": "Toronto, ON, Canada",
            "description": "Design systems.",
            "url": "https://apply.example/R-123",
        },
        {"title": "", "id": ""},  # malformed -> skipped
    ]
    conn = ApifyWorkdayConnector(
        settings=Settings(apify_token="apify_tok", apify_max_run_charge_usd=0.0),
        budget=ApifyBudget(monthly_budget_usd=5.0, ledger_path=tmp_path / "u.json"),
        client=_FakeClient(run, items),
    )
    jobs = conn.fetch(_company())
    assert len(jobs) == 1
    assert jobs[0].source_job_id == "R-123"
    assert jobs[0].title == "Mechanical Engineering Intern"
    assert jobs[0].application_url == "https://apply.example/R-123"


def test_free_actor_cap_is_passed_to_call(tmp_path) -> None:
    client = _FakeClient({"defaultDatasetId": "ds1", "usageTotalUsd": 0.0}, [])
    conn = ApifyWorkdayConnector(
        settings=Settings(apify_token="t", apify_max_run_charge_usd=0.0),
        budget=ApifyBudget(monthly_budget_usd=5.0, ledger_path=tmp_path / "u.json"),
        client=client,
    )
    conn.fetch(_company())
    # The bill cap is handed to the Actor call so a paid run can't surprise-bill.
    assert client._actor.last_opts.get("maxTotalChargeUsd") == 0.0


def test_unfinished_run_is_skipped_not_hung(tmp_path) -> None:
    # A run that did not finish within wait_secs (non-SUCCEEDED status) must be
    # skipped and retried next poll, never block.
    client = _FakeClient(
        {"defaultDatasetId": "ds1", "status": "RUNNING", "usageTotalUsd": 0.0},
        [{"id": "x", "title": "T"}],
    )
    conn = ApifyWorkdayConnector(
        settings=Settings(apify_token="t", apify_max_run_charge_usd=0.0),
        budget=ApifyBudget(monthly_budget_usd=5.0, ledger_path=tmp_path / "u.json"),
        client=client,
    )
    assert conn.fetch(_company()) == []


def test_wait_secs_is_passed_to_call(tmp_path) -> None:
    client = _FakeClient({"defaultDatasetId": "ds1", "usageTotalUsd": 0.0}, [])
    conn = ApifyWorkdayConnector(
        settings=Settings(apify_token="t", apify_run_wait_secs=45),
        budget=ApifyBudget(monthly_budget_usd=5.0, ledger_path=tmp_path / "u.json"),
        client=client,
    )
    conn.fetch(_company())
    assert client._actor.last_opts.get("wait_secs") == 45


def test_paid_run_blocked_when_budget_exhausted(tmp_path) -> None:
    ledger = tmp_path / "u.json"
    budget = ApifyBudget(monthly_budget_usd=5.0, ledger_path=ledger)
    # A connector configured to allow a paid charge, but budget is used up.
    from datetime import date

    budget.record(5.0, today=date.today())  # burn the month
    client = _FakeClient(
        {"defaultDatasetId": "ds1", "usageTotalUsd": 1.0},
        [{"id": "x", "title": "T"}],
    )
    conn = ApifyWorkdayConnector(
        settings=Settings(apify_token="t", apify_max_run_charge_usd=1.0),
        budget=budget,
        client=client,
    )
    # Over budget -> run skipped, no jobs, no crash.
    assert conn.fetch(_company()) == []
