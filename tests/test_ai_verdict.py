"""Tests for the pure Stage-2 AI eligibility verdict (FEAT-003)."""

from __future__ import annotations

from jobtracker.screen.ai_verdict import ai_eligibility


def _result(**overrides: object) -> dict:
    base = {
        "recommendation": "apply",
        "work_authorization_assessment": "Canadian citizen is fine",
        "duration_assessment": "4 months fits",
        "term_assessment": "spring fits",
    }
    base.update(overrides)
    return base


def test_skip_recommendation_is_not_eligible() -> None:
    assert ai_eligibility(_result(recommendation="skip")) is False


def test_apply_with_clean_assessments_is_eligible() -> None:
    assert ai_eligibility(_result(recommendation="apply")) is True


def test_maybe_with_clean_assessments_is_eligible() -> None:
    assert ai_eligibility(_result(recommendation="maybe")) is True


def test_explicit_not_eligible_work_auth_overrides_maybe() -> None:
    result = _result(
        recommendation="maybe",
        work_authorization_assessment="Requires US citizen; candidate is ineligible.",
    )
    assert ai_eligibility(result) is False


def test_explicit_not_eligible_duration_overrides_apply() -> None:
    result = _result(
        recommendation="apply",
        duration_assessment="8-month term is too long for the student.",
    )
    assert ai_eligibility(result) is False


def test_explicit_not_eligible_term_overrides_apply() -> None:
    result = _result(
        recommendation="apply",
        term_assessment="Fall-only role, not available in the student's terms.",
    )
    assert ai_eligibility(result) is False


def test_negative_cue_is_case_insensitive() -> None:
    result = _result(
        recommendation="maybe",
        work_authorization_assessment="CANDIDATE DOES NOT QUALIFY for this posting.",
    )
    assert ai_eligibility(result) is False


def test_missing_assessments_are_non_blocking() -> None:
    result = {"recommendation": "maybe"}
    assert ai_eligibility(result) is True


def test_unknown_assessments_are_non_blocking() -> None:
    result = _result(
        recommendation="maybe",
        work_authorization_assessment="unknown",
        duration_assessment="",
        term_assessment=None,
    )
    assert ai_eligibility(result) is True


def test_empty_result_is_eligible_fallback() -> None:
    # An empty analysis must not hide a survivor (never-empty fallback).
    assert ai_eligibility({}) is True
