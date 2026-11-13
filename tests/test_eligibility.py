"""Tests for the Stage-1 deterministic hard-eligibility verdict."""

from __future__ import annotations

from types import SimpleNamespace

from jobtracker.extract.degree_experience import DegreeExperienceExtraction
from jobtracker.extract.work_auth import (
    INELIGIBLE_ITAR,
    LIKELY_INELIGIBLE_SPONSORSHIP_REQUIRED,
)
from jobtracker.schemas.user_profile import UserProfile
from jobtracker.screen.eligibility import compute_stage1_eligibility

#: Matches config/user.yaml: Spring term, 4-month co-op only.
PROFILE = UserProfile(
    available_terms=["Spring"],
    minimum_duration_months=4,
    maximum_duration_months=4,
    preferred_duration_months=4,
)


def _job(**overrides: object) -> SimpleNamespace:
    base = dict(
        status="active",
        employment_type="internship",
        eligibility_status="possibly_eligible_unknown",
        citizenship_requirement="",
        term_season="unknown",
        duration_months=None,
    )
    base.update(overrides)
    return SimpleNamespace(**base)


def _no_degree_exp() -> DegreeExperienceExtraction:
    return DegreeExperienceExtraction()


def test_full_time_fails_employment_type() -> None:
    verdict = compute_stage1_eligibility(
        _job(employment_type="full_time"), PROFILE, _no_degree_exp()
    )
    assert verdict.eligible is False
    assert "employment_type" in verdict.failed_rules


def test_new_grad_fails_employment_type() -> None:
    verdict = compute_stage1_eligibility(
        _job(employment_type="new_grad"), PROFILE, _no_degree_exp()
    )
    assert verdict.eligible is False
    assert "employment_type" in verdict.failed_rules


def test_unknown_employment_type_not_failed() -> None:
    verdict = compute_stage1_eligibility(
        _job(employment_type="unknown"), PROFILE, _no_degree_exp()
    )
    assert "employment_type" not in verdict.failed_rules


def test_itar_fails() -> None:
    verdict = compute_stage1_eligibility(
        _job(eligibility_status=INELIGIBLE_ITAR), PROFILE, _no_degree_exp()
    )
    assert verdict.eligible is False
    assert "itar" in verdict.failed_rules


def test_itar_company_us_role_fails_even_without_citizenship_text() -> None:
    # A SpaceX US role with NO explicit citizenship text must still be excluded
    # when the company is ITAR-restricted -- this is the SpaceX case.
    verdict = compute_stage1_eligibility(
        _job(country="United States", location="Hawthorne, CA"),
        PROFILE,
        _no_degree_exp(),
        itar_restricted_company=True,
    )
    assert verdict.eligible is False
    assert "itar_company" in verdict.failed_rules


def test_itar_company_unknown_country_role_fails() -> None:
    verdict = compute_stage1_eligibility(
        _job(country=None, location="Remote"),
        PROFILE,
        _no_degree_exp(),
        itar_restricted_company=True,
    )
    assert verdict.eligible is False
    assert "itar_company" in verdict.failed_rules


def test_itar_company_canada_role_survives() -> None:
    # Only the Canada-located roles from an ITAR-restricted company pass.
    verdict = compute_stage1_eligibility(
        _job(country="Canada", location="Toronto, ON, Canada"),
        PROFILE,
        _no_degree_exp(),
        itar_restricted_company=True,
    )
    assert "itar_company" not in verdict.failed_rules


def test_non_itar_company_us_role_not_failed_by_itar_company_rule() -> None:
    # A non-ITAR company's US role is not hit by this rule (leniency intact;
    # the normal sponsorship path still applies).
    verdict = compute_stage1_eligibility(
        _job(country="United States", location="Austin, TX"),
        PROFILE,
        _no_degree_exp(),
        itar_restricted_company=False,
    )
    assert "itar_company" not in verdict.failed_rules


def test_us_citizen_only_fails_citizenship() -> None:
    verdict = compute_stage1_eligibility(
        _job(eligibility_status=LIKELY_INELIGIBLE_SPONSORSHIP_REQUIRED),
        PROFILE,
        _no_degree_exp(),
    )
    assert verdict.eligible is False
    assert "citizenship" in verdict.failed_rules


def test_advanced_degree_fails() -> None:
    degree_exp = DegreeExperienceExtraction(
        requires_advanced_or_completed_degree=True,
        reasons=["Requires an advanced degree: Master's required"],
        evidence=["Master's required"],
    )
    verdict = compute_stage1_eligibility(_job(), PROFILE, degree_exp)
    assert verdict.eligible is False
    assert "degree" in verdict.failed_rules


def test_senior_experience_fails() -> None:
    degree_exp = DegreeExperienceExtraction(
        requires_senior_experience=True,
        min_years_experience=3,
        reasons=["Requires 3+ years of experience"],
        evidence=["3+ years of experience"],
    )
    verdict = compute_stage1_eligibility(_job(), PROFILE, degree_exp)
    assert verdict.eligible is False
    assert "experience" in verdict.failed_rules


def test_incompatible_stated_term_fails() -> None:
    verdict = compute_stage1_eligibility(
        _job(term_season="fall"), PROFILE, _no_degree_exp()
    )
    assert verdict.eligible is False
    assert "term" in verdict.failed_rules


def test_unknown_term_not_failed() -> None:
    verdict = compute_stage1_eligibility(
        _job(term_season="unknown"), PROFILE, _no_degree_exp()
    )
    assert "term" not in verdict.failed_rules


def test_duration_outside_range_fails() -> None:
    verdict = compute_stage1_eligibility(
        _job(duration_months=8), PROFILE, _no_degree_exp()
    )
    assert verdict.eligible is False
    assert "duration" in verdict.failed_rules


def test_duration_below_minimum_fails() -> None:
    verdict = compute_stage1_eligibility(
        _job(duration_months=2), PROFILE, _no_degree_exp()
    )
    assert verdict.eligible is False
    assert "duration" in verdict.failed_rules


def test_unknown_duration_not_failed() -> None:
    verdict = compute_stage1_eligibility(
        _job(duration_months=None), PROFILE, _no_degree_exp()
    )
    assert "duration" not in verdict.failed_rules


def test_removed_job_fails() -> None:
    verdict = compute_stage1_eligibility(
        _job(status="removed"), PROFILE, _no_degree_exp()
    )
    assert verdict.eligible is False
    assert "removed" in verdict.failed_rules


def test_clean_coop_with_unknowns_eligible() -> None:
    verdict = compute_stage1_eligibility(
        _job(
            employment_type="co-op",
            term_season="spring",
            duration_months=4,
        ),
        PROFILE,
        _no_degree_exp(),
    )
    assert verdict.eligible is True
    assert verdict.failed_rules == []
    assert verdict.reasons  # a pass reason is recorded


def test_clean_internship_all_unknown_eligible() -> None:
    verdict = compute_stage1_eligibility(
        _job(employment_type="unknown"), PROFILE, _no_degree_exp()
    )
    assert verdict.eligible is True
    assert verdict.failed_rules == []
