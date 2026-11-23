"""Probe which job boards answer a GitHub Actions runner, and how.

Run from the manual ``discover-boards`` workflow (GitHub's servers), not from a
developer machine. Each board gets a single small request, spaced out, so the
probe itself cannot cause throttling. Writes ``output/runner_probe.json``:

* ``workday``: status of ONE search request per Workday board (plus the
  ``Retry-After`` header and a body snippet), then one retry of the first board
  after a pause, to tell "this server is blocked" from "we sent too much".
* ``other``: RTX (Phenom), Honeywell (Oracle), L3Harris (Radancy).
* ``apify``: one tiny Apify run for a Workday board, to see whether Apify's
  servers get through where the runner does not (costs about $0.001).

Usage: python scripts/runner_probe.py
"""

from __future__ import annotations

import json
import os
import time
from datetime import timedelta
from pathlib import Path

import httpx

UA = "MechEng-Job-Tracker/0.1 (+jobtracker)"
SEARCH = {"limit": 1, "offset": 0, "appliedFacets": {}, "searchText": "intern"}
WORKDAY = [
    ("airbus-canada", "ag", "wd3", "Airbus"),
    ("magna-international", "magna", "wd3", "Magna"),
    ("cae", "cae", "wd3", "career"),
    ("rockwell-automation", "rockwellautomation", "wd1", "external_rockwell_automation"),
    ("ge-vernova", "gevernova", "wd5", "Vernova_ExternalSite"),
    ("nvidia", "nvidia", "wd5", "NVIDIAExternalCareerSite"),
]
PAUSE_SECONDS = 15


def _summary(response: httpx.Response) -> dict:
    keep = ("retry-after", "server", "cf-ray", "content-type", "x-ratelimit-limit")
    return {
        "status": response.status_code,
        "headers": {k: v for k, v in response.headers.items() if k.lower() in keep},
        "body": response.text[:200],
    }


def _call(client: httpx.Client, method: str, url: str, **kwargs) -> dict:
    try:
        return _summary(client.request(method, url, **kwargs))
    except httpx.HTTPError as exc:
        return {"status": None, "error": repr(exc)[:200]}


def probe_workday(client: httpx.Client) -> dict:
    out: dict[str, object] = {}
    for cid, tenant, dc, site in WORKDAY:
        url = f"https://{tenant}.{dc}.myworkdayjobs.com/wday/cxs/{tenant}/{site}/jobs"
        out[cid] = _call(client, "POST", url, json=SEARCH)
        print("workday", cid, out[cid].get("status"), out[cid].get("headers"))
        time.sleep(PAUSE_SECONDS)
    # Retry the first board once after a longer pause.
    time.sleep(60)
    cid, tenant, dc, site = WORKDAY[0]
    url = f"https://{tenant}.{dc}.myworkdayjobs.com/wday/cxs/{tenant}/{site}/jobs"
    out[f"{cid}_retry_after_60s"] = _call(client, "POST", url, json=SEARCH)
    page = f"https://{tenant}.{dc}.myworkdayjobs.com/en-US/{site}"
    out[f"{cid}_public_page"] = _call(client, "GET", page)
    print("workday retry", out[f"{cid}_retry_after_60s"].get("status"))
    return out


def probe_other(client: httpx.Client) -> dict:
    out: dict[str, object] = {}
    out["rtx_widgets"] = _call(
        client,
        "POST",
        "https://careers.rtx.com/widgets",
        json={"ddoKey": "refineSearch", "jobs": True, "from": 0, "size": 1},
    )
    out["rtx_home"] = _call(client, "GET", "https://careers.rtx.com/global/en")
    out["honeywell_oracle"] = _call(
        client,
        "GET",
        "https://ibqbjb.fa.ocs.oraclecloud.com/hcmRestApi/resources/latest/"
        "recruitingCEJobRequisitions",
        params={
            "onlyData": "true",
            "expand": "requisitionList.secondaryLocations",
            "finder": "findReqs;siteNumber=Honeywell,limit=1,offset=0",
        },
    )
    out["l3harris_radancy"] = _call(
        client,
        "GET",
        "https://careers.l3harris.com/en/search-jobs/results",
        params={"ActiveFacetID": "0", "CurrentPage": 1, "RecordsPerPage": 1},
    )
    for key, value in out.items():
        print(key, value.get("status"))
    return out


def probe_apify() -> dict:
    token = os.environ.get("APIFY_TOKEN", "").strip()
    if not token:
        return {"skipped": "no APIFY_TOKEN"}
    from apify_client import ApifyClient

    client = ApifyClient(token=token)
    out: dict[str, object] = {}
    for cid, url in (
        ("magna-international", "https://magna.wd3.myworkdayjobs.com/en-US/Magna?q=intern"),
        (
            "rockwell-automation",
            "https://rockwellautomation.wd1.myworkdayjobs.com/en-US/"
            "external_rockwell_automation?q=intern",
        ),
    ):
        try:
            run = client.actor("gooyer.co/myworkdayjobs").call(
                run_input={
                    "startUrls": [{"url": url}],
                    "pagination": 1,
                    "details": False,
                    "proxyUrls": {"useApifyProxy": True},
                },
                memory_mbytes=1024,
                run_timeout=timedelta(seconds=180),
                wait_duration=timedelta(seconds=200),
            )
            items = client.dataset(run.default_dataset_id).list_items(limit=5).items
            log = client.run(run.id).log().get() or ""
            out[cid] = {
                "status": str(run.status),
                "items": len(items),
                "usage_usd": float(run.usage_total_usd or 0),
                "log_tail": log[-600:],
            }
        except Exception as exc:  # noqa: BLE001 - diagnostic only
            out[cid] = {"error": repr(exc)[:300]}
        print("apify", cid, json.dumps(out[cid])[:300])
    return out


def main() -> None:
    result: dict[str, object] = {}
    with httpx.Client(timeout=30, headers={"User-Agent": UA}) as client:
        try:
            result["runner_ip"] = client.get("https://api.ipify.org").text
        except httpx.HTTPError:
            result["runner_ip"] = None
        result["workday"] = probe_workday(client)
        result["other"] = probe_other(client)
    result["apify"] = probe_apify()
    Path("output").mkdir(exist_ok=True)
    Path("output/runner_probe.json").write_text(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
