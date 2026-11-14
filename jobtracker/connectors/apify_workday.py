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
from datetime import timedelta
from decimal import Decimal

from jobtracker.connectors.base import Connector, RawJob
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
        url = item.get("url") or item.get("externalUrl") or item.get("applyUrl")
        return RawJob(
            source_job_id=sid,
            title=title,
            location=str(
                item.get("location") or item.get("locationsText") or ""
            ).strip(),
            description_html=str(
                item.get("descriptionHtml") or item.get("description") or ""
            ),
            application_url=url,
            source_url=url,
            employment_type_hint=item.get("timeType") or item.get("employmentType"),
            extra={"apify": True},
        )
