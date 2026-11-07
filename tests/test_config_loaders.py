"""Tests for the companies/user config loaders."""

from __future__ import annotations

import pytest

from jobtracker.config import ConfigError, load_companies, load_user_profile
from jobtracker.connectors.registry import is_supported

VALID_COMPANIES = """
companies:
  - id: stripe
    name: Stripe
    ats: greenhouse
    board_token: stripe
    careers_url: https://stripe.com/jobs
    enabled: true
    priority: medium
  - id: acme
    name: Acme
    ats: html
    enabled: true
"""

VALID_USER = """
user:
  name: Test User
  university: University of Waterloo
  program: Mechanical Engineering
  available_terms:
    - Spring
  preferred_duration_months: 4
  profile_version: 2
"""


def _write(tmp_path, name: str, text: str) -> str:
    path = tmp_path / name
    path.write_text(text, encoding="utf-8")
    return str(path)


def test_load_companies_valid(tmp_path):
    path = _write(tmp_path, "companies.yaml", VALID_COMPANIES)
    companies = load_companies(path)
    assert [c.id for c in companies] == ["stripe", "acme"]
    assert companies[0].ats == "greenhouse"
    assert companies[0].priority == "medium"


def test_load_companies_seed_file():
    # task 20b populated companies.yaml with the full design §16.3 target list.
    companies = load_companies("config/companies.yaml")
    by_id = {c.id: c for c in companies}
    # The full target list is large (not the small 3-5 verification seed).
    assert len(companies) >= 50
    # Verified public Greenhouse boards carry the real connector + token.
    assert by_id["spacex"].ats == "greenhouse"
    assert by_id["spacex"].board_token == "spacex"
    assert by_id["archer-aviation"].board_token == "archer56"
    # Companies whose public ATS was not confirmed route to the fallback stub
    # with no fabricated token.
    assert by_id["apple"].ats == "html"
    assert by_id["apple"].board_token is None
    assert by_id["apple"].priority == "low"


def test_seed_companies_supported_ats_have_tokens():
    """Every seed entry wired to a real connector must carry a token.

    Guards against future fabricated/blank tokens: a supported ATS
    (greenhouse/lever/ashby/smartrecruiters) is only fetched when it has a
    non-empty ``board_token``, so an empty one is a silent no-op.
    """
    companies = load_companies("config/companies.yaml")
    supported = [c for c in companies if is_supported(c.ats)]
    # FEAT-003 expanded verified-live coverage well beyond the original 5.
    assert len(supported) >= 15
    blank = [c.id for c in supported if not (c.board_token and c.board_token.strip())]
    assert blank == [], f"supported-ATS companies with blank board_token: {blank}"


def test_load_companies_missing_file():
    with pytest.raises(ConfigError, match="not found"):
        load_companies("config/does-not-exist.yaml")


def test_load_companies_missing_top_level(tmp_path):
    path = _write(tmp_path, "bad.yaml", "foo: bar\n")
    with pytest.raises(ConfigError, match="companies"):
        load_companies(path)


def test_load_companies_invalid_ats(tmp_path):
    text = "companies:\n  - id: x\n    name: X\n    ats: not_an_ats\n"
    path = _write(tmp_path, "bad.yaml", text)
    with pytest.raises(ConfigError, match="Invalid company 'x'"):
        load_companies(path)


def test_load_companies_duplicate_id(tmp_path):
    text = (
        "companies:\n"
        "  - id: x\n    name: X\n    ats: html\n"
        "  - id: x\n    name: Y\n    ats: html\n"
    )
    path = _write(tmp_path, "dup.yaml", text)
    with pytest.raises(ConfigError, match="Duplicate company id"):
        load_companies(path)


def test_load_user_valid(tmp_path):
    path = _write(tmp_path, "user.yaml", VALID_USER)
    profile = load_user_profile(path)
    assert profile.name == "Test User"
    assert profile.available_terms == ["Spring"]
    assert profile.profile_version == 2


def test_load_user_seed_file():
    profile = load_user_profile("config/user.yaml")
    assert profile.university == "University of Waterloo"
    assert profile.program == "Mechanical Engineering"
    assert "Canadian" in profile.citizenship


def test_load_user_missing_top_level(tmp_path):
    path = _write(tmp_path, "bad.yaml", "nope: 1\n")
    with pytest.raises(ConfigError, match="user"):
        load_user_profile(path)


def test_load_user_unknown_field(tmp_path):
    text = "user:\n  name: X\n  bogus_field: 1\n"
    path = _write(tmp_path, "bad.yaml", text)
    with pytest.raises(ConfigError, match="Invalid user profile"):
        load_user_profile(path)
