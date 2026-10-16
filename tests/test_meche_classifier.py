"""Tests for the recall-first mechanical-relevance classifier."""

from __future__ import annotations

from jobtracker.schemas.job import Classification
from jobtracker.screen.meche_classifier import classify


def test_strong_title_is_strong_match() -> None:
    result = classify("Mechanical Engineering Intern", "Design parts in SolidWorks.")
    assert result.classification == Classification.STRONG_MATCH
    assert result.classification_reasons
    assert result.classification_confidence > 0.8


def test_adjacent_role_is_possible_not_rejected() -> None:
    result = classify("Engineering Intern", "Support our product team.")
    assert result.classification == Classification.POSSIBLE_MATCH
    # Never rejected; always carries a reason.
    assert result.classification != Classification.NOT_RELEVANT
    assert result.classification_reasons


def test_skill_signal_is_possible_match() -> None:
    result = classify("Design Co-op", "Experience with CAD and FEA preferred.")
    assert result.classification == Classification.POSSIBLE_MATCH
    assert result.classification_reasons


def test_clearly_unrelated_is_not_relevant() -> None:
    result = classify(
        "Sales Intern",
        "Join our Sales and Marketing team to close deals and manage accounts.",
    )
    assert result.classification == Classification.NOT_RELEVANT
    assert result.classification_reasons


def test_ambiguous_defaults_to_weak_not_rejected() -> None:
    result = classify("Operations Intern", "Help the operations team.")
    # No clear unrelated signal in both title and description -> weak, surfaced.
    assert result.classification == Classification.WEAK_MATCH
    assert result.classification != Classification.NOT_RELEVANT
