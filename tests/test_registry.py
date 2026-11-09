"""Registry tests: connector resolution + detect_ats URL recognition."""

from __future__ import annotations

from jobtracker.connectors.fallback import FallbackConnector
from jobtracker.connectors.feed import JsonLdConnector, RssConnector, SitemapConnector
from jobtracker.connectors.http import HttpClient
from jobtracker.connectors.registry import detect_ats, get_connector, is_supported
from jobtracker.connectors.workable import WorkableConnector
from jobtracker.connectors.workday import WorkdayConnector
from jobtracker.schemas.company import ATSType
from jobtracker.settings import Settings


def _client() -> HttpClient:
    return HttpClient(Settings(rate_limit_per_host=0.0), sleep=lambda _s: None)


def test_new_connectors_are_registered() -> None:
    client = _client()
    assert is_supported(ATSType.WORKDAY.value)
    assert is_supported(ATSType.WORKABLE.value)
    assert is_supported(ATSType.RSS.value)
    assert is_supported(ATSType.SITEMAP.value)
    assert is_supported(ATSType.JSON_LD.value)
    assert isinstance(get_connector(ATSType.WORKDAY.value, client), WorkdayConnector)
    assert isinstance(get_connector(ATSType.WORKABLE.value, client), WorkableConnector)
    assert isinstance(get_connector(ATSType.RSS.value, client), RssConnector)
    assert isinstance(get_connector(ATSType.SITEMAP.value, client), SitemapConnector)
    assert isinstance(get_connector(ATSType.JSON_LD.value, client), JsonLdConnector)
    # Unknown / html still routes to the fail-soft fallback stub.
    assert isinstance(get_connector(ATSType.HTML.value, client), FallbackConnector)
    client.close()


def test_detect_ats_workday() -> None:
    result = detect_ats(
        "https://nvidia.wd5.myworkdayjobs.com/NVIDIAExternalCareerSite"
    )
    assert result.ats == ATSType.WORKDAY.value
    assert result.board_token == "nvidia/wd5/NVIDIAExternalCareerSite"


def test_detect_ats_workday_cxs_path() -> None:
    result = detect_ats(
        "https://stryker.wd1.myworkdayjobs.com/wday/cxs/stryker/StrykerCareers/jobs"
    )
    assert result.ats == ATSType.WORKDAY.value
    assert result.board_token == "stryker/wd1/StrykerCareers"


def test_detect_ats_workable() -> None:
    assert detect_ats("https://acme.workable.com/jobs").ats == ATSType.WORKABLE.value
    result = detect_ats("https://apply.workable.com/acme/")
    assert result.ats == ATSType.WORKABLE.value
    assert result.board_token == "acme"


def test_detect_ats_feeds() -> None:
    assert detect_ats("https://acme.example/careers/feed").ats == ATSType.RSS.value
    assert (
        detect_ats("https://acme.example/jobs/sitemap.xml").ats
        == ATSType.SITEMAP.value
    )


def test_detect_ats_unknown_is_html() -> None:
    assert detect_ats("https://careers.apple.com").ats == ATSType.HTML.value
