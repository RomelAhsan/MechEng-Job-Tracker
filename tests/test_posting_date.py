"""Tests for the relative "Posted N Days Ago" posting-date parser."""

from __future__ import annotations

from datetime import UTC, datetime

from jobtracker.normalize.posting_date import (
    is_relative_lower_bound,
    parse_relative_posted,
)

NOW = datetime(2026, 2, 1, 12, 0, tzinfo=UTC)


def test_parse_posted_n_days_ago() -> None:
    result = parse_relative_posted("Posted 25 Days Ago", now=NOW)
    assert result == datetime(2026, 1, 7, 12, 0, tzinfo=UTC)


def test_parse_posted_today_and_yesterday() -> None:
    assert parse_relative_posted("Posted Today", now=NOW) == NOW
    assert parse_relative_posted("Posted Yesterday", now=NOW) == datetime(
        2026, 1, 31, 12, 0, tzinfo=UTC
    )


def test_parse_posted_30_plus_days_counts_as_30() -> None:
    # The absolute date stays at the 30-day lower bound so the freshness cutoff
    # keeps hiding it; only the label treats it as "30+".
    result = parse_relative_posted("Posted 30+ Days Ago", now=NOW)
    assert result == datetime(2026, 1, 2, 12, 0, tzinfo=UTC)


def test_is_relative_lower_bound_detects_plus_and_or_more() -> None:
    assert is_relative_lower_bound("Posted 30+ Days Ago") is True
    assert is_relative_lower_bound("30 or more days ago") is True
    # An exact count is NOT a lower bound.
    assert is_relative_lower_bound("Posted 30 Days Ago") is False
    assert is_relative_lower_bound("Posted 25 Days Ago") is False
    assert is_relative_lower_bound("Posted Today") is False
    assert is_relative_lower_bound(None) is False


def test_parse_or_more_variant_resolves_to_lower_bound() -> None:
    assert parse_relative_posted("30 or more days ago", now=NOW) == datetime(
        2026, 1, 2, 12, 0, tzinfo=UTC
    )


def test_parse_unrecognized_returns_none() -> None:
    assert parse_relative_posted(None) is None
    assert parse_relative_posted("") is None
    assert parse_relative_posted("some other text") is None
