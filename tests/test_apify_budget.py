"""Tests for the daily Apify usage budget (the user's cap-per-day design).

The rule: daily_cap = monthly_budget / days_in_month; unused rolls forward, but
the month is still the ceiling. A day can spend up to (daily_cap * day_number)
minus what has already been spent this month.
"""

from __future__ import annotations

from datetime import date

from jobtracker.services.apify_budget import ApifyBudget


def _budget(tmp_path, monthly=30.0) -> ApifyBudget:
    return ApifyBudget(
        monthly_budget_usd=monthly, ledger_path=tmp_path / "usage.json"
    )


def test_daily_cap_is_month_budget_over_days(tmp_path) -> None:
    b = _budget(tmp_path, monthly=30.0)
    # November has 30 days -> $1/day.
    d = b.check(today=date(2026, 11, 1))
    assert round(d.daily_cap_usd, 2) == 1.00


def test_day_one_allowance_is_one_daily_cap(tmp_path) -> None:
    b = _budget(tmp_path, monthly=30.0)
    d = b.check(today=date(2026, 11, 1))
    assert round(d.allowance_usd, 2) == 1.00  # one day's share accrued


def test_unused_allowance_rolls_forward(tmp_path) -> None:
    b = _budget(tmp_path, monthly=30.0)
    # Nothing spent by day 5 -> 5 days of $1 accrued and available.
    d = b.check(today=date(2026, 11, 5))
    assert round(d.allowance_usd, 2) == 5.00


def test_spend_reduces_allowance(tmp_path) -> None:
    b = _budget(tmp_path, monthly=30.0)
    b.record(3.0, today=date(2026, 11, 2))
    # By day 3: accrued 3 * $1 = $3, spent $3 -> $0 left.
    d = b.check(today=date(2026, 11, 3))
    assert round(d.allowance_usd, 2) == 0.00
    assert d.exhausted is True
    # Day 4 accrues another $1.
    d4 = b.check(today=date(2026, 11, 4))
    assert round(d4.allowance_usd, 2) == 1.00


def test_month_budget_is_the_ceiling(tmp_path) -> None:
    b = _budget(tmp_path, monthly=30.0)
    # Late in the month with nothing spent, allowance never exceeds the full
    # monthly budget (not day_number * cap beyond the month total).
    d = b.check(today=date(2026, 11, 30))
    assert round(d.allowance_usd, 2) == 30.00
    b.record(30.0, today=date(2026, 11, 30))
    assert b.check(today=date(2026, 11, 30)).allowance_usd == 0.0


def test_can_spend_respects_allowance(tmp_path) -> None:
    b = _budget(tmp_path, monthly=30.0)
    assert b.can_spend(0.0, today=date(2026, 11, 1)) is True  # free always ok
    assert b.can_spend(0.50, today=date(2026, 11, 1)) is True
    assert b.can_spend(2.00, today=date(2026, 11, 1)) is False  # > $1 day-1 cap


def test_spend_is_scoped_to_the_current_month(tmp_path) -> None:
    b = _budget(tmp_path, monthly=30.0)
    b.record(10.0, today=date(2026, 10, 15))  # previous month
    d = b.check(today=date(2026, 11, 1))
    # October spend does not reduce November's allowance.
    assert round(d.allowance_usd, 2) == 1.00


def test_corrupt_ledger_does_not_crash(tmp_path) -> None:
    path = tmp_path / "usage.json"
    path.write_text("{ not valid json", encoding="utf-8")
    b = ApifyBudget(monthly_budget_usd=30.0, ledger_path=path)
    # Treated as empty rather than raising.
    assert b.check(today=date(2026, 11, 1)).allowance_usd == 1.00
