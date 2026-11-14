"""Regression tests for the bugs found in the live data.

* Locations: "Hawthorne, CA" is California; SuccessFactors' "CA" after a
  province is Canada; Workday gives a city plus a country descriptor.
* Terms: each employer's season naming maps to the student's terms.
* Degrees: "Bachelor's degree in ..." as a requirement removes a job, but
  "pursuing a Bachelor's" does not.
* SuccessFactors RSS parsing.
"""

from __future__ import annotations

import pytest

from jobtracker.connectors.successfactors import parse_feed
from jobtracker.extract.degree_experience import extract_degree_experience
from jobtracker.extract.term import extract_term, term_set
from jobtracker.normalize.normalizer import infer_employment_type, split_location


@pytest.mark.parametrize(
    ("location", "country"),
    [
        ("Hawthorne, CA", "United States"),
        ("Vandenberg, CA", "United States"),
        ("Renfrew, ON, CA, K7V 0C2", "Canada"),
        ("Montréal, QC, CA", "Canada"),
        ("Nashville, TN, US", "United States"),
        ("CA-ON-Toronto", "Canada"),
        ("Markham", "Canada"),
        ("Dallas, United States of America", "United States"),
        ("Pune, India", "India"),
        ("Remote", None),
    ],
)
def test_split_location_country(location: str, country: str | None) -> None:
    assert split_location(location)[2] == country


@pytest.mark.parametrize(
    ("title", "country", "terms"),
    [
        ("Hardware Engineering Intern - Spring/Summer 2027", "United States", {"winter", "spring"}),
        ("Engineering Intern/Co-Op - Spring 2027", "United States", {"winter"}),
        ("Mechanical Co-op - Spring 2027", "Canada", {"spring"}),
        ("Mechanical Engineering Intern (Summer 2027)", "United States", {"spring"}),
        ("Fall 2027 Co-op", "Canada", {"fall"}),
        ("Winter 2028 Co-op Student", "Canada", {"winter"}),
        ("Stagiaire - Été 2027", "Canada", {"spring"}),
        ("Spring Design Engineer", "United States", set()),
    ],
)
def test_term_mapping(title: str, country: str, terms: set[str]) -> None:
    result = extract_term(title, "", country=country)
    assert set(result.terms) == terms
    assert term_set(result.stored_value) == terms


def test_month_range_maps_to_term() -> None:
    result = extract_term(
        "Mechanical Intern", "Work term: May to August 2027.", country="Canada"
    )
    assert result.terms == ["spring"]


def test_may_as_a_verb_is_not_a_month() -> None:
    result = extract_term(
        "Engineer", "Starting salary may vary - by location.", country="Canada"
    )
    assert result.terms == []


def test_required_bachelors_is_a_completed_degree() -> None:
    result = extract_degree_experience(
        "Welding Engineer",
        "BASIC QUALIFICATIONS:\n- Bachelor's degree in an engineering discipline",
    )
    assert result.requires_advanced_or_completed_degree is True


def test_pursuing_bachelors_is_not_a_completed_degree() -> None:
    result = extract_degree_experience(
        "Mechanical Engineering Intern",
        "Currently pursuing a Bachelor's degree in mechanical engineering.",
    )
    assert result.requires_advanced_or_completed_degree is False


def test_graduate_student_title_is_excluded() -> None:
    result = extract_degree_experience("PhD Intern, Thermal", "")
    assert result.requires_advanced_or_completed_degree


@pytest.mark.parametrize(
    "title",
    ["New Graduate Engineer, Mechanical", "Graduate Engineer - Mechanical", "EIT, Mechanical"],
)
def test_new_grad_titles(title: str) -> None:
    assert infer_employment_type(title, "", None).value == "new_grad"


_RSS = """<?xml version="1.0"?><rss><channel>
<item><title><![CDATA[Mechanical Engineering Co-op Student (Renfrew, ON, CA, K7V 0C2)]]></title>
<description><![CDATA[<p>4-month work term, May to August 2027.</p>]]></description>
<link>https://jobs.opg.com/job/Renfrew-Co-op-ON/607092017/?feedId=null&amp;utm_source=J2WRSS</link>
<pubDate>Sat, 14 Nov 2026 2:00:00 GMT</pubDate></item>
</channel></rss>"""


def test_successfactors_feed_parsing() -> None:
    jobs = parse_feed(_RSS)
    assert len(jobs) == 1
    job = jobs[0]
    assert job.title == "Mechanical Engineering Co-op Student"
    assert job.location == "Renfrew, ON, CA, K7V 0C2"
    assert job.source_job_id == "607092017"
    assert job.application_url == "https://jobs.opg.com/job/Renfrew-Co-op-ON/607092017/"
    assert job.posted_at is not None and job.posted_at.year == 2026
    assert split_location(job.location)[2] == "Canada"


def test_split_location_iso_codes_and_us_cities() -> None:
    from jobtracker.normalize.normalizer import split_location

    assert split_location("Colon, MEX, MX, 76270")[2] == "Mexico"
    assert split_location("Pittsburgh")[2] == "United States"
    assert split_location("New York City")[2] == "United States"
    assert split_location("Fort Wayne, IN, US")[2] == "United States"
    assert split_location("Renfrew, ON, CA, K7V 0C2")[2] == "Canada"


def test_term_title_wins_over_graduation_dates() -> None:
    from jobtracker.extract.term import extract_term

    result = extract_term(
        "AI Workflows Intern [Summer 2027]",
        "Expected graduation date between Fall 2027 and Summer 2028.",
        country="United States",
    )
    assert result.terms == ["spring"]
    body_only = extract_term(
        "Mechanical Intern", "Expected graduation Fall 2029.", country="Canada"
    )
    assert body_only.terms == []


def test_graduation_window_conflict() -> None:
    from jobtracker.screen.eligibility import graduation_window_conflict as g

    assert g("Expected graduation between Fall 2027 and Summer 2028.", 2031)
    assert g("Students graduating in 2026 or 2027.", 2031)
    assert g("Must be graduating in December 2027 or later.", 2031) is None
    assert g("Expected graduation: 2030-2032.", 2031) is None
    assert g("Expected graduation between 2027 and 2028.", None) is None


def test_french_stage_title_is_student_signal() -> None:
    from jobtracker.screen.eligibility import _TITLE_STAGE_RE

    assert _TITLE_STAGE_RE.search("Stage - Tunnels et ouvrages souterrains")
    assert _TITLE_STAGE_RE.search("Stage en génie mécanique (Hiver 2027)")
    assert not _TITLE_STAGE_RE.search("Multi-Stage Compressor Engineer")
    assert not _TITLE_STAGE_RE.search("Stage 2 Test Engineer")


def test_title_field_decides_relevance() -> None:
    from jobtracker.screen.meche_classifier import classify

    noisy = "Work with mechanical teams using CAD and SolidWorks."
    assert classify("Software Engineering Intern", noisy).classification.value == (
        "not_relevant"
    )
    assert classify("Intern, Immigration Law", noisy).classification.value == (
        "not_relevant"
    )
    assert classify(
        "Hardware Engineering Intern (Critical Infrastructure Communications)", ""
    ).classification.value == "strong_match"
    assert classify(
        "Stagiaire, Génie hydromécanique (Hiver 2027)", ""
    ).classification.value == "strong_match"


def test_other_discipline_role_noun_not_rescued_by_context() -> None:
    from jobtracker.screen.meche_classifier import classify

    for title in (
        "Electrical Engineer Intern, Robotics and Surgery Engineering",
        "Software Engineer Intern, Robotics",
        "Neuroengineer Intern",
    ):
        assert classify(title, "robotics CAD").classification.value == "not_relevant"
    assert classify(
        "Mechanical Engineering Intern, Robotics and Surgery Engineering", ""
    ).classification.value == "strong_match"


def test_ai_failed_rules_force_not_eligible() -> None:
    from jobtracker.screen.ai_verdict import ai_eligibility

    assert ai_eligibility({"eligible": True, "recommendation": "apply",
                           "failed_rules": [7]}) is False
    assert ai_eligibility({"eligible": True, "recommendation": "apply",
                           "failed_rules": []}) is True


def test_workday_links_get_locale_prefix() -> None:
    from jobtracker.connectors.workday import with_locale

    url = "https://gevernova.wd5.myworkdayjobs.com/Vernova_ExternalSite/job/Rochester/X_R1"
    assert with_locale(url) == (
        "https://gevernova.wd5.myworkdayjobs.com/en-US/Vernova_ExternalSite/job/Rochester/X_R1"
    )
    assert with_locale(with_locale(url)) == with_locale(url)
    assert with_locale("https://boards.greenhouse.io/x/jobs/1") == (
        "https://boards.greenhouse.io/x/jobs/1"
    )


def test_split_location_space_separated_country_and_city_only() -> None:
    from jobtracker.normalize.normalizer import split_location

    assert split_location("Wilmington NC USA") == ("Wilmington", "NC", "United States")
    assert split_location("Charleroi USA")[2] == "United States"
    assert split_location("Paris France")[2] == "France"
    assert split_location("Schenectady")[2] == "United States"
    assert split_location("Brossard")[2] == "Canada"
    assert split_location("London")[2] is None
