"""Tests for citizenship/sponsorship extraction and derived eligibility."""

from __future__ import annotations

from jobtracker.extract.work_auth import (
    ELIGIBLE,
    INELIGIBLE_ITAR,
    LIKELY_INELIGIBLE_SPONSORSHIP_REQUIRED,
    POSSIBLY_ELIGIBLE_UNKNOWN,
    extract_work_auth,
)
from jobtracker.schemas.job import VisaSponsorship


def test_canadian_role_eligible() -> None:
    result = extract_work_auth(
        "This is a co-op based in Toronto, Ontario.", country="Canada"
    )
    assert result.eligibility_status == ELIGIBLE
    assert result.reasons


def test_us_role_no_sponsorship_likely_ineligible() -> None:
    result = extract_work_auth(
        "This role is based in California. We do not offer visa sponsorship.",
        country="United States",
    )
    assert result.visa_sponsorship == VisaSponsorship.NOT_PROVIDED
    assert result.eligibility_status == LIKELY_INELIGIBLE_SPONSORSHIP_REQUIRED
    assert result.sponsorship_evidence  # exact quote present


def test_us_role_sponsorship_provided_eligible() -> None:
    result = extract_work_auth(
        "Based in Austin, Texas. Visa sponsorship is available for this role.",
        country="United States",
    )
    assert result.visa_sponsorship == VisaSponsorship.PROVIDED
    assert result.eligibility_status == ELIGIBLE
    assert result.sponsorship_evidence


def test_missing_info_possibly_eligible_unknown() -> None:
    result = extract_work_auth(
        "Join our mechanical engineering team.", country="United States"
    )
    assert result.visa_sponsorship == VisaSponsorship.UNKNOWN
    assert result.eligibility_status == POSSIBLY_ELIGIBLE_UNKNOWN
    # Never auto-ineligible on missing info.
    assert result.eligibility_status != LIKELY_INELIGIBLE_SPONSORSHIP_REQUIRED


def test_itar_us_person_requirement_ineligible() -> None:
    result = extract_work_auth(
        "This position requires ITAR compliance; applicants must be U.S. persons.",
        country="United States",
    )
    assert result.eligibility_status == INELIGIBLE_ITAR
    assert result.citizenship_requirement  # non-empty evidence quote
    assert result.reasons


def test_export_control_us_citizen_ineligible() -> None:
    result = extract_work_auth(
        "Due to export control regulations, candidates must be U.S. citizens.",
        country="United States",
    )
    assert result.eligibility_status == INELIGIBLE_ITAR
    assert result.citizenship_requirement


def test_plain_us_citizen_only_is_itar() -> None:
    result = extract_work_auth(
        "Applicants must be a U.S. citizen.", country="United States"
    )
    assert result.eligibility_status == INELIGIBLE_ITAR
    assert result.citizenship_requirement


def test_itar_takes_precedence_over_sponsorship() -> None:
    # An ITAR role is not rescued by a sponsorship-available phrase.
    result = extract_work_auth(
        "Visa sponsorship is available. This role requires ITAR compliance.",
        country="United States",
    )
    assert result.eligibility_status == INELIGIBLE_ITAR


def test_normal_roles_not_itar_excluded() -> None:
    canadian = extract_work_auth(
        "Co-op based in Toronto, Ontario.", country="Canada"
    )
    assert canadian.eligibility_status == ELIGIBLE

    missing_info = extract_work_auth(
        "Join our mechanical engineering team.", country="United States"
    )
    assert missing_info.eligibility_status == POSSIBLY_ELIGIBLE_UNKNOWN
    assert missing_info.eligibility_status != INELIGIBLE_ITAR


def test_evidence_present_or_unknown() -> None:
    # When sponsorship is unknown, evidence is empty (unknown is first-class).
    unknown = extract_work_auth("A generic description.", country="Canada")
    assert unknown.visa_sponsorship == VisaSponsorship.UNKNOWN
    assert unknown.sponsorship_evidence == ""

    # When a sponsorship conclusion is drawn, evidence is always present.
    provided = extract_work_auth(
        "Visa sponsorship is provided.", country="United States"
    )
    assert provided.visa_sponsorship != VisaSponsorship.UNKNOWN
    assert provided.sponsorship_evidence != ""
