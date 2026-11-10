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


def test_itar_skill_mention_not_excluded() -> None:
    # A topic/skill mention of ITAR is NOT a barrier; the role stays visible.
    result = extract_work_auth(
        "Experience with ITAR-regulated hardware is a plus.",
        country="United States",
    )
    assert result.eligibility_status == POSSIBLY_ELIGIBLE_UNKNOWN
    assert result.eligibility_status != INELIGIBLE_ITAR


def test_export_control_topic_mention_not_excluded() -> None:
    result = extract_work_auth(
        "Familiarity with export control compliance processes preferred.",
        country="United States",
    )
    assert result.eligibility_status == POSSIBLY_ELIGIBLE_UNKNOWN
    assert result.eligibility_status != INELIGIBLE_ITAR


def test_ear_training_mention_not_excluded() -> None:
    result = extract_work_auth(
        "EAR training will be provided.", country="United States"
    )
    assert result.eligibility_status == POSSIBLY_ELIGIBLE_UNKNOWN
    assert result.eligibility_status != INELIGIBLE_ITAR


def test_sponsorship_with_itar_skill_mention_stays_visible() -> None:
    # Sponsorship offered + a mere ITAR skill mention -> NOT excluded; the
    # sponsorship-based derivation applies and the role is eligible.
    result = extract_work_auth(
        "Visa sponsorship available. ITAR experience a plus.",
        country="United States",
    )
    assert result.eligibility_status != INELIGIBLE_ITAR
    assert result.visa_sponsorship == VisaSponsorship.PROVIDED
    assert result.eligibility_status == ELIGIBLE


def test_inclusive_us_persons_mention_not_excluded() -> None:
    # Inclusive, non-barrier framing of "US persons" must stay visible.
    result = extract_work_auth(
        "Position is open to US persons (citizens, permanent residents, "
        "asylees, and refugees).",
        country="United States",
    )
    assert result.eligibility_status != INELIGIBLE_ITAR


def test_itar_framed_as_requirement_is_excluded() -> None:
    # A bare keyword DOES become a barrier when framed as a requirement.
    result = extract_work_auth(
        "Due to ITAR, applicants must be authorized to access controlled data.",
        country="United States",
    )
    assert result.eligibility_status == INELIGIBLE_ITAR


def test_us_persons_only_barrier_still_excluded() -> None:
    result = extract_work_auth(
        "This role is restricted to U.S. persons only.",
        country="United States",
    )
    assert result.eligibility_status == INELIGIBLE_ITAR


def test_barrier_wins_over_sponsorship_phrase() -> None:
    # A genuine barrier is not rescued by a sponsorship-available phrase.
    result = extract_work_auth(
        "Visa sponsorship is available. Applicants must be U.S. citizens.",
        country="United States",
    )
    assert result.eligibility_status == INELIGIBLE_ITAR


def test_active_security_clearance_required_is_barrier() -> None:
    result = extract_work_auth(
        "Applicants must have an active security clearance.",
        country="United States",
    )
    assert result.eligibility_status == INELIGIBLE_ITAR
    assert result.citizenship_requirement


def test_security_clearance_required_phrase_is_barrier() -> None:
    result = extract_work_auth(
        "An active secret security clearance is required for this role.",
        country="United States",
    )
    assert result.eligibility_status == INELIGIBLE_ITAR


def test_must_be_able_to_obtain_clearance_is_barrier() -> None:
    result = extract_work_auth(
        "Candidate must be able to obtain a security clearance.",
        country="United States",
    )
    assert result.eligibility_status == INELIGIBLE_ITAR


def test_top_secret_clearance_required_is_barrier() -> None:
    result = extract_work_auth(
        "Top secret clearance required.", country="United States"
    )
    assert result.eligibility_status == INELIGIBLE_ITAR


def test_clearance_on_unknown_country_is_barrier() -> None:
    # Unknown country + explicit clearance requirement is still a hard barrier.
    result = extract_work_auth(
        "Must hold an active security clearance.", country=None
    )
    assert result.eligibility_status == INELIGIBLE_ITAR


def test_clearance_mere_mention_not_barrier() -> None:
    result = extract_work_auth(
        "An active security clearance is a plus.", country="United States"
    )
    assert result.eligibility_status != INELIGIBLE_ITAR
    assert result.eligibility_status == POSSIBLY_ELIGIBLE_UNKNOWN


def test_clearance_preferred_not_barrier() -> None:
    result = extract_work_auth(
        "Security clearance preferred but not required.",
        country="United States",
    )
    assert result.eligibility_status != INELIGIBLE_ITAR


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
