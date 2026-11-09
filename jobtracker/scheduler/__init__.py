"""Background polling scheduler (FEAT-003).

Boards do not push new postings to us, so true-instant delivery is impossible.
Instead we poll each company as fast as is safe and cap every company's
interval at ``scheduler_max_interval_minutes`` (default 60) so NO company ever
waits longer than ~1 hour. The scheduling decisions are factored into pure,
testable functions in :mod:`jobtracker.scheduler.runner`.
"""

from __future__ import annotations

from jobtracker.scheduler.runner import (
    ScheduledJob,
    build_jobs,
    compute_interval,
    run_scheduler,
)

__all__ = [
    "ScheduledJob",
    "build_jobs",
    "compute_interval",
    "run_scheduler",
]
