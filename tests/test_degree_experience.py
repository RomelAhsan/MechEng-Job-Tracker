"""Tests for the deterministic degree-level / experience extractor (Stage 1)."""

from __future__ import annotations

from jobtracker.extract.degree_experience import extract_degree_experience


def test_masters_required_detected() -> None:
    result = extract_degree_experience(
        "Design Engineer", "A Master's degree is required for this role."
    )
    assert result.requires_advanced_or_completed_degree is True
    assert result.evidence


def test_ms_required_detected() -> None:
    result = extract_degree_experience(
        "R&D Engineer", "MS required in Mechanical Engineering."
    )
    assert result.requires_advanced_or_completed_degree is True
    assert result.evidence


def test_phd_required_detected() -> None:
    result = extract_degree_experience(
        "Research Scientist", "PhD required; doctorate in a related field."
    )
    assert result.requires_advanced_or_completed_degree is True
    assert result.evidence


def test_mba_required_detected() -> None:
    result = extract_degree_experience(
        "Program Lead", "An MBA is required to lead cross-functional programs."
    )
    assert result.requires_advanced_or_completed_degree is True
    assert result.evidence


def test_must_have_completed_detected() -> None:
    result = extract_degree_experience(
        "Engineer", "Candidates must have completed their undergraduate degree."
    )
    assert result.requires_advanced_or_completed_degree is True
    assert result.evidence


def test_bachelors_degree_required_detected() -> None:
    result = extract_degree_experience(
        "Mechanical Engineer", "Bachelor's degree required in engineering."
    )
    assert result.requires_advanced_or_completed_degree is True
    assert result.evidence


def test_masters_preferred_not_detected() -> None:
    # A merely PREFERRED (not required) advanced degree is ambiguous for a
    # first-year and must SURVIVE Stage 1 so it reaches the AI stage.
    result = extract_degree_experience(
        "Design Engineer", "A Master's degree is preferred for this role."
    )
    assert result.requires_advanced_or_completed_degree is False
    assert result.evidence == []


def test_phd_preferred_not_detected() -> None:
    result = extract_degree_experience(
        "Research Scientist", "A PhD is strongly preferred for this role."
    )
    assert result.requires_advanced_or_completed_degree is False


def test_mba_preferred_not_detected() -> None:
    result = extract_degree_experience(
        "Program Lead", "An MBA is preferred for this program role."
    )
    assert result.requires_advanced_or_completed_degree is False


def test_working_toward_bachelors_not_detected() -> None:
    result = extract_degree_experience(
        "Mechanical Engineering Intern",
        "Ideal for students working toward a Bachelor's degree.",
    )
    assert result.requires_advanced_or_completed_degree is False


def test_currently_pursuing_not_detected() -> None:
    result = extract_degree_experience(
        "Co-op Student",
        "You are currently pursuing a Bachelor's degree in engineering.",
    )
    assert result.requires_advanced_or_completed_degree is False


def test_currently_enrolled_not_detected() -> None:
    result = extract_degree_experience(
        "Intern",
        "Must be enrolled in an accredited engineering program; "
        "Bachelor's degree in progress.",
    )
    assert result.requires_advanced_or_completed_degree is False


def test_senior_role_detected() -> None:
    # Title-only input with an empty description: the common MechE title shape
    # "Senior Mechanical Engineer" (an adjective between the seniority word and
    # the role noun) must be flagged so it never leaks onto the no-key page.
    result = extract_degree_experience("Senior Mechanical Engineer", "")
    assert result.requires_senior_experience is True
    assert result.evidence


def test_principal_title_with_qualifier_detected() -> None:
    result = extract_degree_experience("Principal Software Engineer", "")
    assert result.requires_senior_experience is True
    assert result.evidence


def test_staff_title_with_qualifier_detected() -> None:
    result = extract_degree_experience("Staff Research Scientist", "")
    assert result.requires_senior_experience is True
    assert result.evidence


def test_staff_role_detected() -> None:
    result = extract_degree_experience(
        "Staff Engineer", "This is a staff engineer position."
    )
    assert result.requires_senior_experience is True


def test_principal_role_detected() -> None:
    result = extract_degree_experience(
        "Principal Engineer", "We are hiring a principal engineer."
    )
    assert result.requires_senior_experience is True


def test_three_plus_years_detected() -> None:
    result = extract_degree_experience(
        "Engineer", "Requires 3+ years of experience in product design."
    )
    assert result.requires_senior_experience is True
    assert result.min_years_experience == 3
    assert result.evidence


def test_minimum_of_two_years_detected() -> None:
    result = extract_degree_experience(
        "Engineer", "Minimum of 2 years of relevant experience required."
    )
    assert result.requires_senior_experience is True
    assert result.min_years_experience == 2


def test_two_years_of_experience_preferred_not_detected() -> None:
    # Consistent with the degree "preferred" rule: a nice-to-have experience
    # line is ambiguous and must survive Stage 1 rather than hard-excluding an
    # intern-welcoming posting.
    result = extract_degree_experience(
        "Engineer", "2 years of experience preferred."
    )
    assert result.min_years_experience is None
    assert result.requires_senior_experience is False


def test_two_years_of_experience_a_plus_not_detected() -> None:
    result = extract_degree_experience(
        "Engineer", "2 years of experience is a plus."
    )
    assert result.min_years_experience is None
    assert result.requires_senior_experience is False


def test_two_years_of_experience_required_detected() -> None:
    # Requirement framing (required / minimum of / at least) still hard-fails.
    result = extract_degree_experience(
        "Engineer", "2 years of experience required."
    )
    assert result.min_years_experience == 2
    assert result.requires_senior_experience is True


def test_clean_intern_description_all_false() -> None:
    result = extract_degree_experience(
        "Mechanical Engineering Intern",
        "Great opportunity for a first-year student. You will support the "
        "design team, learn CAD, and help with prototyping. No prior "
        "experience required.",
    )
    assert result.requires_advanced_or_completed_degree is False
    assert result.requires_senior_experience is False
    assert result.min_years_experience is None
    assert result.evidence == []
    assert result.reasons == []


def test_lead_a_project_not_flagged_as_senior() -> None:
    # "lead a project" is a responsibility, not a seniority level.
    result = extract_degree_experience(
        "Mechanical Engineering Intern",
        "You will help lead a project and present results to the team.",
    )
    assert result.requires_senior_experience is False


def test_intern_reporting_to_senior_colleague_not_flagged() -> None:
    # A senior COLLEAGUE mentioned in the description (whom the intern reports
    # to) is boilerplate, NOT a seniority prerequisite on the candidate. The
    # role being hired is an intern, so this must survive Stage 1.
    result = extract_degree_experience(
        "Design Intern", "Report to a senior design manager."
    )
    assert result.requires_senior_experience is False
    assert result.evidence == []


def test_coop_reporting_to_senior_engineer_not_flagged() -> None:
    result = extract_degree_experience(
        "Mechanical Engineering Co-op", "You will report to a senior design engineer."
    )
    assert result.requires_senior_experience is False
    assert result.evidence == []


def test_intern_mentored_by_principal_engineer_not_flagged() -> None:
    result = extract_degree_experience(
        "Summer Intern",
        "Supported by a principal systems engineer who mentors all co-ops.",
    )
    assert result.requires_senior_experience is False
    assert result.evidence == []


def test_coop_shadowing_staff_engineer_not_flagged() -> None:
    result = extract_degree_experience(
        "Co-op Student",
        "Shadow a staff manufacturing engineer during your term.",
    )
    assert result.requires_senior_experience is False
    assert result.evidence == []


def test_senior_level_candidate_in_description_still_flagged() -> None:
    # Requirement framing on the CANDIDATE ("senior-level") is a genuine
    # prerequisite and still hard-fails even from the description.
    result = extract_degree_experience(
        "Mechanical Engineer",
        "We are seeking a senior-level candidate for this position.",
    )
    assert result.requires_senior_experience is True
    assert result.evidence


def test_experienced_professional_in_description_still_flagged() -> None:
    result = extract_degree_experience(
        "Mechanical Engineer",
        "This role is for an experienced professional with a strong portfolio.",
    )
    assert result.requires_senior_experience is True
    assert result.evidence
