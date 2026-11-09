"""Scheduler interval computation + job-planning (pure, no timers/network).

The scheduling decisions are factored into pure functions so they can be tested
without real APScheduler timers, threads, or network calls.
"""

from __future__ import annotations

from jobtracker.db.models import Company
from jobtracker.scheduler.runner import build_jobs, compute_interval
from jobtracker.settings import Settings


def _settings(**overrides: object) -> Settings:
    base = dict(
        scheduler_enabled=True,
        scheduler_max_interval_minutes=60,
        scheduler_high_interval_minutes=15,
        scheduler_medium_interval_minutes=45,
        scheduler_low_interval_minutes=60,
    )
    base.update(overrides)
    return Settings(**base)  # type: ignore[arg-type]


def _company(**kwargs: object) -> Company:
    defaults = dict(id="acme", name="Acme", ats="greenhouse", enabled=True, active=True)
    defaults.update(kwargs)
    return Company(**defaults)  # type: ignore[arg-type]


def test_priority_defaults_used_when_no_explicit_interval() -> None:
    settings = _settings()
    assert compute_interval(_company(priority="high"), settings) == 15
    assert compute_interval(_company(priority="medium"), settings) == 45
    assert compute_interval(_company(priority="low"), settings) == 60


def test_missing_priority_defaults_to_medium() -> None:
    settings = _settings()
    assert compute_interval(_company(priority=None), settings) == 45


def test_explicit_interval_is_honored() -> None:
    settings = _settings()
    assert compute_interval(_company(priority="low", check_interval_minutes=20), settings) == 20


def test_interval_never_exceeds_the_global_cap() -> None:
    settings = _settings(scheduler_max_interval_minutes=60)
    # Explicit 240 min is capped to 60 so nothing waits longer than ~1h.
    assert compute_interval(_company(check_interval_minutes=240), settings) == 60
    # A low-priority default above the cap would also be capped.
    capped = _settings(scheduler_max_interval_minutes=30, scheduler_low_interval_minutes=60)
    assert compute_interval(_company(priority="low"), capped) == 30


def test_interval_floor_is_one_minute() -> None:
    settings = _settings()
    assert compute_interval(_company(check_interval_minutes=0), settings) == 45
    assert compute_interval(_company(check_interval_minutes=-5), settings) == 45


def test_build_jobs_skips_disabled_and_inactive() -> None:
    settings = _settings()
    companies = [
        _company(id="a", priority="high"),
        _company(id="b", priority="low", enabled=False),
        _company(id="c", priority="medium", active=False),
        _company(id="d", priority="medium", check_interval_minutes=10),
    ]
    jobs = build_jobs(companies, settings)
    assert [j.company_id for j in jobs] == ["a", "d"]
    by_id = {j.company_id: j.interval_minutes for j in jobs}
    assert by_id == {"a": 15, "d": 10}
