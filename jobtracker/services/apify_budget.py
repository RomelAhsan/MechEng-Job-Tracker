"""Daily Apify usage budget (the user's 'cap per day, not per month' design).

Apify's free plan renews a fixed USD of usage credit each month and simply
PAUSES (never bills) when exhausted. The user wants finer control than that:
spend at most the *daily* share of the monthly budget, so a single day cannot
burn the whole month. Concretely:

    daily_cap = monthly_budget / days_in_month

Unused budget ROLLS FORWARD to later days in the same month (an allowance that
accrues), but a day may never spend more than everything accrued up to and
including that day minus what has already been spent this month -- i.e. the
rolled-over design, bounded so the whole month's budget is still the ceiling.
If a day's needed work exceeds its available allowance, the excess is deferred
to the next day; if even that would overload, the caller is told so it can warn
rather than silently overspend.

Everything is accounted in USD. For FREE Actors the charge is 0.0, so the cap
is effectively "unlimited free runs" -- the ledger still records runs so usage
is visible, and the cap bites only if a paid Actor is ever configured.

State lives in a small JSON ledger file (default under the DB/output dir) keyed
by ``YYYY-MM-DD`` so it survives across CLI/scheduler invocations.
"""

from __future__ import annotations

import calendar
import json
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path

DEFAULT_LEDGER_PATH = "state/apify_usage.json"


@dataclass
class BudgetDecision:
    """Outcome of an allowance check for 'today'."""

    #: USD the caller may spend right now (accrued allowance minus month spend).
    allowance_usd: float
    #: The flat per-day share (monthly_budget / days_in_month).
    daily_cap_usd: float
    #: USD already spent this month.
    spent_this_month_usd: float
    #: True when there is zero allowance left right now.
    exhausted: bool


class ApifyBudget:
    """Persistent daily/rolling USD budget tracker for Apify usage."""

    def __init__(
        self,
        *,
        monthly_budget_usd: float,
        ledger_path: str | Path = DEFAULT_LEDGER_PATH,
    ) -> None:
        self.monthly_budget_usd = float(monthly_budget_usd)
        self.ledger_path = Path(ledger_path)

    # --- ledger persistence ------------------------------------------------
    def _load(self) -> dict[str, float]:
        if not self.ledger_path.exists():
            return {}
        try:
            data = json.loads(self.ledger_path.read_text(encoding="utf-8"))
            return {str(k): float(v) for k, v in data.items()}
        except (json.JSONDecodeError, ValueError, OSError):
            # A corrupt ledger must never crash a poll; treat as empty (the only
            # downside is forgetting this month's spend, which is safe-ish for a
            # free-tier cap and self-heals next month).
            return {}

    def _save(self, ledger: dict[str, float]) -> None:
        self.ledger_path.parent.mkdir(parents=True, exist_ok=True)
        self.ledger_path.write_text(json.dumps(ledger, indent=2), encoding="utf-8")

    # --- math --------------------------------------------------------------
    @staticmethod
    def _today() -> date:
        return datetime.now(UTC).date()

    def _month_keys(self, today: date) -> list[str]:
        """YYYY-MM-DD keys for every day of ``today``'s month up to today."""
        return [
            f"{today.year:04d}-{today.month:02d}-{d:02d}"
            for d in range(1, today.day + 1)
        ]

    def _daily_cap(self, today: date) -> float:
        days = calendar.monthrange(today.year, today.month)[1]
        return self.monthly_budget_usd / days

    def _spent_this_month(self, ledger: dict[str, float], today: date) -> float:
        prefix = f"{today.year:04d}-{today.month:02d}-"
        return sum(v for k, v in ledger.items() if k.startswith(prefix))

    # --- per-company run spacing -------------------------------------------
    def due(self, company_id: str, interval_seconds: float) -> bool:
        """True when ``company_id`` has not been fetched in ``interval_seconds``."""
        last = self._load().get(f"last_run:{company_id}")
        if last is None:
            return True
        return datetime.now(UTC).timestamp() - last >= interval_seconds

    def mark_run(self, company_id: str) -> None:
        ledger = self._load()
        ledger[f"last_run:{company_id}"] = datetime.now(UTC).timestamp()
        self._save(ledger)

    # --- public API --------------------------------------------------------
    def check(self, *, today: date | None = None) -> BudgetDecision:
        """Return today's available allowance (accrued cap minus month spend).

        Allowance = (daily_cap * days_elapsed_this_month) - spent_this_month,
        clamped to [0, remaining monthly budget]. This is exactly the
        "roll unused forward, but the month is still the ceiling" rule.
        """
        today = today or self._today()
        ledger = self._load()
        daily_cap = self._daily_cap(today)
        accrued = daily_cap * today.day  # cap rolled forward through today
        spent = self._spent_this_month(ledger, today)
        remaining_month = max(0.0, self.monthly_budget_usd - spent)
        allowance = max(0.0, min(accrued - spent, remaining_month))
        return BudgetDecision(
            allowance_usd=round(allowance, 4),
            daily_cap_usd=round(daily_cap, 4),
            spent_this_month_usd=round(spent, 4),
            exhausted=allowance <= 0.0,
        )

    def can_spend(self, cost_usd: float, *, today: date | None = None) -> bool:
        """True if ``cost_usd`` fits within today's allowance (0 always fits)."""
        if cost_usd <= 0:
            return True
        return cost_usd <= self.check(today=today).allowance_usd

    def record(self, cost_usd: float, *, today: date | None = None) -> None:
        """Add ``cost_usd`` to today's ledger entry (free runs record 0.0)."""
        today = today or self._today()
        ledger = self._load()
        key = today.isoformat()
        ledger[key] = round(ledger.get(key, 0.0) + max(0.0, float(cost_usd)), 6)
        self._save(ledger)
