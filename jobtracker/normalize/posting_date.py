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
* ``"Posted 30+ Days Ago"`` -> treated as 30 days ago, but flagged a LOWER
  BOUND (the true age is "30 or more"). The absolute ``posted_at`` stays at
  now-30d so the 14-day freshness cutoff still hides it; the lower-bound signal
  only changes the label so the website can read "Posted 30+ days ago" instead
  of a precise but wrong "Posted 30 days ago".

Why keep ``posted_at`` at now-30d instead of threading a lower-bound flag all
the way to the database? Workday is the only source that emits a "+"/"or more"
relative bucket, and it does so only at its 30-day cap. Carrying a boolean
through RawJob -> CanonicalJob -> a new DB column + migration -> JobView just to
flip a label would be disproportionate, so the lower-bound case is recognized at
the presentation layer from the :data:`RELATIVE_LOWER_BOUND_DAYS` cap (see
``web_export_service`` / ``JobCard``). The 14-day hide is unaffected.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta

#: "Posted 25 Days Ago", "25 days ago", "Posted 30+ Days Ago" -> N.
#: Group 2 captures the "+"/"or more" marker that makes N a lower bound.
_RELATIVE_DAYS_RE = re.compile(
    r"(\d+)\s*(\+|\bor\s+more\b)?\s*days?\s*ago", re.IGNORECASE
)
_TODAY_RE = re.compile(r"\bposted\s+today\b|\btoday\b", re.IGNORECASE)
_YESTERDAY_RE = re.compile(r"\byesterday\b", re.IGNORECASE)

#: Day count at which a relative "N+ Days Ago" bucket is emitted. Workday's
#: only "or more" bucket is "30+ Days Ago", so a relative posting resolved to
#: exactly this many days is treated as a lower bound by the presentation layer
#: (the website renders "Posted 30+ days ago").
RELATIVE_LOWER_BOUND_DAYS = 30


def is_relative_lower_bound(value: str | None) -> bool:
    """True when ``value`` is a "N+ Days Ago" / "N or more days ago" bucket.

    Lets a caller distinguish Workday's "Posted 30+ Days Ago" (age is "at
    least 30 days") from an exact "Posted 30 Days Ago" without changing the
    absolute date :func:`parse_relative_posted` returns.
    """
    if not value:
        return False
    match = _RELATIVE_DAYS_RE.search(value)
    return bool(match and match.group(2))


def parse_relative_posted(
    value: str | None, *, now: datetime | None = None
) -> datetime | None:
    """Parse a relative "Posted N Days Ago"-style string into a datetime.

    Returns ``None`` when ``value`` carries no recognizable relative-age signal
    (the caller should then fall back to an absolute date field). ``now``
    defaults to the current UTC time (the fetch time). A "30+" / "or more"
    bucket resolves to its lower bound (now - N days); use
    :func:`is_relative_lower_bound` to tell the two apart for labelling.
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
