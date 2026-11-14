"""Parse absolute and relative posting dates into an absolute ``posted_at``.

Several boards expose the true posting date only as a relative English string
such as Workday's ``"Posted 25 Days Ago"`` / ``"Posted Today"`` /
``"Posted Yesterday"`` / ``"Posted 30+ Days Ago"``. The freshness cutoff and
the "Posted X days ago" label on the website care about the TRUE posting date,
not when we fetched the posting, so we turn these into an absolute datetime by
subtracting N days from the fetch time.

Rules (per the user instruction):

* ``"Posted Today"`` -> 0 days ago
* ``"Posted Yesterday"`` -> 1 day ago
* ``"Posted N Days Ago"`` -> N days ago
* ``"Posted 30+ Days Ago"`` -> treated as 30 days ago (a lower bound; anything
  at or beyond the 14-day cutoff is hidden regardless)
"""

from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta

#: "Posted 25 Days Ago", "25 days ago", "Posted 30+ Days Ago" -> N
_RELATIVE_DAYS_RE = re.compile(r"(\d+)\s*\+?\s*days?\s*ago", re.IGNORECASE)
_TODAY_RE = re.compile(r"\bposted\s+today\b|\btoday\b", re.IGNORECASE)
_YESTERDAY_RE = re.compile(r"\byesterday\b", re.IGNORECASE)


def parse_relative_posted(
    value: str | None, *, now: datetime | None = None
) -> datetime | None:
    """Parse a relative "Posted N Days Ago"-style string into a datetime.

    Returns ``None`` when ``value`` carries no recognizable relative-age signal
    (the caller should then fall back to an absolute date field). ``now``
    defaults to the current UTC time (the fetch time).
    """
    if not value:
        return None
    text = value.strip()
    if not text:
        return None
    now = now or datetime.now(UTC)

    match = _RELATIVE_DAYS_RE.search(text)
    if match:
        days = int(match.group(1))
        return now - timedelta(days=days)
    if _YESTERDAY_RE.search(text):
        return now - timedelta(days=1)
    if _TODAY_RE.search(text):
        return now
    return None
