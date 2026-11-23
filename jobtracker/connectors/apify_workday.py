"""Apify-backed Workday connector for JS-locked career sites.

Many big employers (WSP, Stantec, OPG, Teck, ...) run Workday boards whose job
data is only reachable through the site's JavaScript, so the direct public
Workday CXS connector cannot find their tenant. Apify runs a reputable
third-party Actor on ITS infrastructure to fetch those boards, so the user's
own IP is never involved (this is the user's stated reason the no-scraping rule
does not apply to a third-party fetch service).

KEY-READY + FREE-TIER-ONLY + BUDGET-CAPPED:

* With no ``APIFY_TOKEN`` configured, :meth:`fetch` returns an empty list (the
  company simply yields no jobs, like any unreachable board) and never raises.
* A hard per-run charge cap (``apify_max_run_charge_usd``, default 0.0 = free
  only) is passed to every Actor call so a paid run can NEVER surprise-bill.
* The daily :class:`ApifyBudget` is consulted before every run; when today's
  allowance is exhausted the run is skipped and retried a later day.

A company uses this connector by setting ``ats: apify_workday`` in
companies.yaml with its ``careers_url`` pointing at the Workday site (the Actor
takes the URL and discovers the tenant itself).
"""

from __future__ import annotations

import logging
import re
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from jobtracker.connectors.base import Connector, RawJob
from jobtracker.connectors.workday import (
    WorkdayCoordinates,
    parse_workday_url,
    with_locale,
)
from jobtracker.connectors.workday import _coordinates as workday_coordinates
from jobtracker.connectors.workday_targeting import TargetingPlan, public_search_urls
from jobtracker.normalize.posting_date import parse_relative_posted
from jobtracker.schemas.company import CompanyConfig
from jobtracker.services.apify_budget import ApifyBudget
from jobtracker.settings import Settings, get_settings

logger = logging.getLogger("jobtracker.connectors.apify_workday")


def _field(obj: object, *names: str) -> object:
    """Read a field from an apify-client v3 model or a plain dict."""
    for name in names:
        if isinstance(obj, dict) and name in obj:
            return obj[name]
        if hasattr(obj, name):
            return getattr(obj, name)
    return None


def with_locale_url(url: str) -> str:
    return with_locale(url)


#: Student keywords used for a board with no cached filters. "intern" and
#: "co-op" are left out: Workday matches them against most of a board.
_NO_PLAN_KEYWORDS = ("internship", "coop", "student", "stagiaire")


def _parse_date(value: object) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip().replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


__all__ = ["ApifyWorkdayConnector", "WorkdayCoordinates", "with_locale_url"]


class ApifyWorkdayConnector(Connector):
    """Fetch a Workday board via a free Apify Actor, under a daily budget cap."""

    ats = "apify_workday"

    def __init__(
        self,
        http: object | None = None,  # unused; kept for registry compatibility
        *,
        settings: Settings | None = None,
        budget: ApifyBudget | None = None,
        client: object | None = None,
    ) -> None:
        self._settings = settings or get_settings()
        self._budget = budget or ApifyBudget(
            monthly_budget_usd=self._settings.apify_monthly_budget_usd
        )
        self._client = client  # injectable for tests (no network)

    # --- client / availability -------------------------------------------
    @property
    def available(self) -> bool:
        return bool((self._settings.apify_token or "").strip())

    def _get_client(self) -> object | None:
        if self._client is not None:
            return self._client
        if not self.available:
            return None
        try:
            from apify_client import ApifyClient
        except ImportError:
            logger.warning(
                "apify-client is not installed; skipping Apify Workday fetch."
            )
            return None
        self._client = ApifyClient(token=self._settings.apify_token)
        return self._client

    # --- fetch -------------------------------------------------------------
    def fetch(self, company: CompanyConfig) -> list[RawJob]:
        if not self.available:
            logger.info(
                "APIFY_TOKEN not set; %s (apify_workday) yields no jobs.",
                company.id,
            )
            return []

        # Budget gate: never exceed today's rolled allowance. A free Actor costs
        # 0.0 and always passes; the cap only bites for a paid Actor.
        max_charge = float(self._settings.apify_max_run_charge_usd)
        if max_charge > 0 and not self._budget.can_spend(max_charge):
            decision = self._budget.check()
            logger.warning(
                "Apify daily budget exhausted (allowance $%.4f, spent $%.4f this "
                "month); deferring %s to a later day.",
                decision.allowance_usd,
                decision.spent_this_month_usd,
                company.id,
            )
            return []

        client = self._get_client()
        if client is None:
            return []

        careers_url = company.careers_url
        if not careers_url:
            logger.warning("%s has no careers_url for Apify Workday.", company.id)
            return []

        actor_id = self._settings.apify_workday_actor.replace("~", "/")
        # Input per the Actor's published schema (startUrls + proxyUrls are
        # required; pagination 0 = all pages; details = full descriptions).
        run_input = {
            "startUrls": [{"url": careers_url}],
            "pagination": 0,
            "details": True,
            "proxyUrls": {"useApifyProxy": True},
        }
        try:
            run = client.actor(actor_id).call(  # type: ignore[attr-defined]
                run_input=run_input,
                # Hard bill cap (0 = free Actors only) and a bounded wait so a
                # slow third-party run never stalls the whole poll.
                max_total_charge_usd=Decimal(str(max_charge)),
                wait_duration=timedelta(seconds=int(self._settings.apify_run_wait_secs)),
            )
        except Exception as exc:  # noqa: BLE001 - one board failing is fail-soft.
            logger.warning("Apify run failed for %s: %s", company.id, exc)
            return []
        status = _field(run, "status")
        if not run or (status not in (None, "SUCCEEDED")):
            logger.info(
                "Apify run for %s not finished (status=%s); will retry next poll.",
                company.id,
                status,
            )
            return []

        charged = float(_field(run, "usage_total_usd", "usageTotalUsd") or 0.0)
        self._budget.record(charged)

        dataset_id = _field(run, "default_dataset_id", "defaultDatasetId")
        if not dataset_id:
            return []
        try:
            items = client.dataset(dataset_id).list_items().items  # type: ignore[attr-defined]
        except Exception as exc:  # noqa: BLE001
            logger.warning("Apify dataset read failed for %s: %s", company.id, exc)
            return []

        return [self._to_raw(item) for item in items if self._to_raw(item) is not None]

    # --- fallback for Workday boards that refuse GitHub's servers ---------
    #
    # Workday answers GitHub's servers with "429 Too Many Requests" (a
    # Cloudflare block). Apify's servers get through. One cheap LISTING run
    # (no descriptions) covers every blocked board; a second DETAILS run then
    # fetches only postings we have not stored yet. The listing doubles as the
    # "still open?" check: a stored posting missing from it has closed.

    def can_start(self) -> bool:
        """True when a token is set and today's free allowance allows a run."""
        if not self.available:
            return False
        decision = self._budget.check()
        ok = not decision.exhausted and decision.allowance_usd >= float(
            self._settings.apify_min_allowance_usd
        )
        if not ok:
            logger.info(
                "Apify allowance $%.4f is below the minimum; Workday boards "
                "that refuse GitHub stay unchecked this run.",
                decision.allowance_usd,
            )
        return ok

    def due(self, company_id: str) -> bool:
        """True when ``company_id`` was not fetched through Apify recently."""
        interval = float(self._settings.apify_fallback_interval_hours) * 3600
        return self._budget.due(company_id, interval)

    def mark_fetched(self, company_ids: list[str]) -> None:
        for company_id in company_ids:
            self._budget.mark_run(company_id)

    def _start(self, urls: list[str], *, details: bool) -> str | None:
        client = self._get_client()
        if client is None or not urls:
            return None
        actor_id = self._settings.apify_workday_actor.replace("~", "/")
        try:
            run = client.actor(actor_id).start(  # type: ignore[attr-defined]
                run_input={
                    "startUrls": [{"url": u} for u in urls],
                    "pagination": 0,
                    "details": details,
                    "proxyUrls": {"useApifyProxy": True},
                },
                max_total_charge_usd=(
                    Decimal(str(self._settings.apify_max_run_charge_usd))
                    if self._settings.apify_max_run_charge_usd > 0
                    else None
                ),
                # Bounded run: Apify stops it itself, so a stuck run can never
                # keep spending credit.
                run_timeout=timedelta(
                    seconds=int(self._settings.apify_fallback_wait_secs) + 120
                ),
                memory_mbytes=1024,
            )
        except Exception as exc:  # noqa: BLE001 - fail-soft.
            logger.warning("Could not start Apify run: %s", exc)
            return None
        run_id = _field(run, "id")
        logger.info(
            "Started Apify %s run %s over %d URLs.",
            "details" if details else "listing",
            run_id,
            len(urls),
        )
        return str(run_id) if run_id else None

    def start_listing(self, urls: list[str]) -> str | None:
        """Start a listing run (titles and paths only) over search URLs."""
        return self._start(urls, details=False)

    def start_details(self, urls: list[str]) -> str | None:
        """Start a run that fetches full details for specific job URLs."""
        return self._start(urls, details=True)

    def wait_items(self, run_id: str, *, wait_secs: int) -> list[dict] | None:
        """Wait up to ``wait_secs`` for a run; return its items or ``None``.

        An unfinished run is aborted so it stops using credit. Usage is always
        recorded in the budget ledger.
        """
        client = self._get_client()
        if client is None:
            return None
        run_client = client.run(run_id)  # type: ignore[attr-defined]
        try:
            run = run_client.wait_for_finish(
                wait_duration=timedelta(seconds=max(1, int(wait_secs)))
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("Waiting for Apify run %s failed: %s", run_id, exc)
            run = None
        status = _field(run, "status") if run else None
        if status != "SUCCEEDED":
            try:
                run_client.abort()
                run = run_client.get()
            except Exception:  # noqa: BLE001
                pass
        charged = (
            float(_field(run, "usage_total_usd", "usageTotalUsd") or 0.0) if run else 0.0
        )
        self._budget.record(charged)
        if status != "SUCCEEDED":
            logger.info("Apify run %s ended as %s; its results are not used.", run_id, status)
            return None
        dataset_id = _field(run, "default_dataset_id", "defaultDatasetId")
        if not dataset_id:
            return []
        try:
            items = client.dataset(dataset_id).list_items().items  # type: ignore[attr-defined]
        except Exception as exc:  # noqa: BLE001
            logger.warning("Apify dataset read failed for run %s: %s", run_id, exc)
            return None
        logger.info(
            "Apify run %s returned %d items (charged $%.4f).", run_id, len(items), charged
        )
        return [i for i in items if isinstance(i, dict)]

    @staticmethod
    def search_urls(company: CompanyConfig, plan: TargetingPlan | None) -> list[str]:
        """Public search URLs covering a board's student postings in Canada/US."""
        coords = workday_coordinates(company)
        if coords is None:
            return []
        if plan is None:
            # No cached filters: precise student keywords only (no country
            # filter is possible without the board's facet ids).
            plan = TargetingPlan(full_board=False, keywords=list(_NO_PLAN_KEYWORDS))
        return public_search_urls(coords.base, coords.site, plan)

    @staticmethod
    def board_key(url: str | None) -> tuple[str, str] | None:
        """``(host, site)`` for a Workday job or search URL, to group items."""
        if not url:
            return None
        coords = parse_workday_url(re.sub(r"/[a-z]{2}-[A-Z]{2}/", "/", url, count=1))
        if coords is None:
            return None
        return coords.base.lower(), coords.site.lower()

    @staticmethod
    def item_path(item: dict) -> str | None:
        """The ``/job/...`` path of a listing or detail item."""
        path = item.get("externalPath")
        if isinstance(path, str) and path.startswith("/job/"):
            return path
        for key in ("externalUrl", "url"):
            url = item.get(key)
            if isinstance(url, str) and "/job/" in url:
                return url[url.index("/job/"):]
        return None

    @staticmethod
    def detail_to_raw(item: dict) -> RawJob | None:
        """Map a details-run item (Workday ``jobPostingInfo`` fields) to RawJob.

        Mirrors the direct connector so the same posting gets the same
        ``source_job_id`` whichever way it was fetched.
        """
        path = ApifyWorkdayConnector.item_path(item)
        title = str(item.get("title") or "").strip()
        if not path or not title:
            return None
        url = item.get("externalUrl") or item.get("url")
        url = with_locale_url(re.sub(r"/wday/cxs/[^/]+/", "/", str(url))) if url else None
        location = str(item.get("location") or item.get("locationsText") or "").strip()
        country = item.get("country")
        if isinstance(country, dict):
            country = country.get("descriptor")
        if isinstance(country, str) and country and country.lower() not in location.lower():
            location = f"{location}, {country}" if location else country
        posted = _parse_date(item.get("startDate")) or parse_relative_posted(
            item.get("postedOn")
        )
        source_job_id = item.get("id") or item.get("jobReqId") or path
        return RawJob(
            source_job_id=str(source_job_id),
            title=title,
            location=location,
            description_html=str(item.get("jobDescription") or ""),
            application_url=url,
            source_url=url,
            posted_at=posted,
            employment_type_hint=item.get("timeType"),
            extra={"apify": True},
        )

    @staticmethod
    def _to_raw(item: dict) -> RawJob | None:
        """Map one Apify dataset record to a RawJob (tolerant of key variants)."""
        if not isinstance(item, dict):
            return None
        sid = str(
            item.get("id")
            or item.get("jobId")
            or item.get("externalPath")
            or item.get("url")
            or ""
        ).strip()
        title = str(item.get("title") or item.get("jobTitle") or "").strip()
        if not sid or not title:
            return None
        url = item.get("externalUrl") or item.get("url") or item.get("applyUrl")
        if isinstance(url, str):
            # The Actor reports the JSON API address
            # (".../wday/cxs/{tenant}/{site}/job/..."); link the public page.
            url = re.sub(r"/wday/cxs/[^/]+/", "/", url)
        return RawJob(
            source_job_id=sid,
            title=title,
            location=str(
                item.get("location")
                or item.get("locationText")
                or item.get("locationsText")
                or ""
            ).strip(),
            description_html=str(
                item.get("descriptionHtml") or item.get("description") or ""
            ),
            application_url=url,
            source_url=url,
            employment_type_hint=item.get("timeType") or item.get("employmentType"),
            extra={"apify": True},
        )
