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
        title="Mechanical Engineering Co-op Student",
        description="",
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


def test_itar_company_explicit_us_citizen_role_hard_fails() -> None:
    # An EXPLICIT US-citizen barrier is still hard-failed deterministically
    # (via the persisted ITAR status), with or without the company flag.
    verdict = compute_stage1_eligibility(
        _job(
            country="United States",
            location="Hawthorne, CA",
            eligibility_status=INELIGIBLE_ITAR,
        ),
        PROFILE,
        _no_degree_exp(),
        itar_restricted_company=True,
    )
    assert verdict.eligible is False
    assert "itar" in verdict.failed_rules


def test_itar_company_us_role_hard_excluded_by_default() -> None:
    # A US-located role at an ITAR company with NO explicit openness is now
    # hard-excluded at Stage 1 (the user is categorically ineligible; we don't
    # leave it for the quota-limited AI). This is the SpaceX case.
    verdict = compute_stage1_eligibility(
        _job(
            country="United States",
            location="Hawthorne, CA",
            title="Propulsion Engineer (Raptor)",
            description="Design rocket engines. Bachelor's in engineering.",
        ),
        PROFILE,
        _no_degree_exp(),
        itar_restricted_company=True,
    )
    assert verdict.eligible is False
    assert "itar_company" in verdict.failed_rules


def test_itar_company_us_role_survives_only_if_explicitly_open() -> None:
    # The one honest exception: a US ITAR-company role that explicitly offers
    # sponsorship / welcomes non-US persons is NOT hard-excluded; it survives
    # and is flagged for the AI to double-check.
    verdict = compute_stage1_eligibility(
        _job(
            country="United States",
            location="Hawthorne, CA",
            title="Mechanical Engineering Intern",
            description="Open to international applicants; visa sponsorship available.",
        ),
        PROFILE,
        _no_degree_exp(),
        itar_restricted_company=True,
    )
    assert "itar_company" not in verdict.failed_rules
    assert verdict.itar_company_ambiguous is True


def test_itar_company_canada_role_survives() -> None:
    verdict = compute_stage1_eligibility(
        _job(country="Canada", location="Toronto, ON, Canada"),
        PROFILE,
        _no_degree_exp(),
        itar_restricted_company=True,
    )
    assert "itar_company" not in verdict.failed_rules


def test_non_itar_company_us_role_not_hit_by_itar_rule() -> None:
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


# --- Screening tiers (level 1 = rules only; level 2 = AI confirms) ---------


def test_canadian_intern_title_is_level_one_auto() -> None:
    verdict = compute_stage1_eligibility(
        _job(title="Mechanical Engineering Intern", country="Canada", location="Toronto, ON"),
        PROFILE,
        _no_degree_exp(),
    )
    assert verdict.eligible is True
    assert verdict.tier == "auto"


def test_us_intern_goes_to_ai() -> None:
    verdict = compute_stage1_eligibility(
        _job(title="Mechanical Engineering Intern", country="United States"),
        PROFILE,
        _no_degree_exp(),
    )
    assert verdict.eligible is True
    assert verdict.tier == "ai"


def test_student_signal_only_in_description_goes_to_ai() -> None:
    verdict = compute_stage1_eligibility(
        _job(
            title="Mechanical Design Assistant",
            description=(
                "This co-op term is open to students currently enrolled in an "
                "engineering program."
            ),
            employment_type="unknown",
            country="Canada",
        ),
        PROFILE,
        _no_degree_exp(),
    )
    assert verdict.eligible is True
    assert verdict.tier == "ai"


def test_no_student_signal_is_removed() -> None:
    verdict = compute_stage1_eligibility(
        _job(
            title="Propulsion Engineer (Raptor)",
            description="Design rocket engines for Starship.",
            employment_type="unknown",
            country="United States",
        ),
        PROFILE,
        _no_degree_exp(),
    )
    assert verdict.eligible is False
    assert "not_student_role" in verdict.failed_rules


def test_foreign_country_is_removed() -> None:
    verdict = compute_stage1_eligibility(
        _job(title="Mechanical Engineering Intern", country="India", location="Pune, India"),
        PROFILE,
        _no_degree_exp(),
    )
    assert verdict.eligible is False
    assert "foreign_country" in verdict.failed_rules


def test_multi_term_posting_passes_if_any_term_fits() -> None:
    # "Spring/Summer 2027" at a US employer = winter + spring terms.
    verdict = compute_stage1_eligibility(
        _job(term_season="winter,spring"), PROFILE, _no_degree_exp()
    )
    assert "term" not in verdict.failed_rules


def test_us_spring_semester_only_fails_term() -> None:
    # US "Spring 2027" = January-May = the student's winter term only.
    verdict = compute_stage1_eligibility(
        _job(term_season="winter"), PROFILE, _no_degree_exp()
    )
    assert "term" in verdict.failed_rules
