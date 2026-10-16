"""Tests for deterministic term/duration extraction over the SPEC phrases."""

from __future__ import annotations

import pytest

from jobtracker.extract.term import extract_term
from jobtracker.schemas.job import TermSeason


def test_four_month_spring_2027() -> None:
    result = extract_term("4-month Spring 2027 co-op")
    assert result.term_season == TermSeason.SPRING
    assert result.duration_months == 4
    assert result.term_start_date.year == 2027
    assert result.term_start_date.month == 5
    assert result.term_end_date.month == 8
    assert result.evidence


def test_may_august_2027() -> None:
    result = extract_term("May-August 2027")
    assert result.term_season == TermSeason.SPRING
    assert result.term_start_date.year == 2027
    assert result.term_start_date.month == 5
    assert result.term_end_date.month == 8
    assert result.evidence


def test_twelve_month_internship_beginning_may_2027() -> None:
    result = extract_term("12-month internship beginning May 2027")
    assert result.term_season == TermSeason.SPRING
    assert result.duration_months == 12
    assert result.term_start_date.year == 2027
    assert result.term_start_date.month == 5
    assert result.evidence


def test_winter_2027_work_term() -> None:
    result = extract_term("Winter 2027 work term")
    assert result.term_season == TermSeason.WINTER
    assert result.term_start_date.month == 1
    assert result.term_end_date.month == 4
    assert result.evidence


def test_fall_2026_coop() -> None:
    result = extract_term("Fall 2026 co-op")
    assert result.term_season == TermSeason.FALL
    assert result.term_start_date.year == 2026
    assert result.term_start_date.month == 9
    assert result.term_end_date.month == 12
    assert result.evidence


def test_september_to_december() -> None:
    result = extract_term("September to December")
    assert result.term_season == TermSeason.FALL
    # No year supplied -> dates unresolved, but season + evidence present.
    assert result.term_start_date is None
    assert result.evidence


def test_unknown_term() -> None:
    result = extract_term("Software Engineer, Backend Platform")
    assert result.term_season == TermSeason.UNKNOWN
    assert result.term_start_date is None
    assert result.term_end_date is None
    assert result.evidence == ""


@pytest.mark.parametrize(
    "phrase",
    [
        "4-month Spring 2027 co-op",
        "May-August 2027",
        "12-month internship beginning May 2027",
        "Winter 2027 work term",
        "Fall 2026 co-op",
        "September to December",
    ],
)
def test_evidence_present_when_not_unknown(phrase: str) -> None:
    result = extract_term(phrase)
    assert result.term_season != TermSeason.UNKNOWN
    assert result.evidence != ""
