"""Probe how the Apify Workday Actor handles filtered search URLs (run from CI).

Three tiny Actor runs (well under $0.01 in total):

* ``facets``: a Workday search URL carrying student + Canada/US facet filters
  as query parameters, without details. The run log reports how many postings
  the Actor saw, which shows whether it honoured the filters.
* ``details``: two filtered boards in ONE run with full details, to measure
  the cost of fetching descriptions and to see which fields come back.
* ``job_url``: a single job page URL, to confirm the Actor can fetch just the
  postings we have not seen yet.

Writes ``output/apify_probe.json``. Skipped when no APIFY_TOKEN is set.
"""

from __future__ import annotations

import json
import os
from datetime import timedelta
from pathlib import Path

ACTOR = os.environ.get("APIFY_WORKDAY_ACTOR", "gooyer.co~myworkdayjobs").replace("~", "/")
AIRBUS = (
    "https://ag.wd3.myworkdayjobs.com/en-US/Airbus"
    "?workerSubType=f5811cef9cb50193723ed01d470a6e15"
    "&locationCountry=a30a87ed25634629aa6c3958aa2b91ea"
    "&locationCountry=bc33aa3152ec42d4995f4791a106ed09"
)
MAGNA = (
    "https://magna.wd3.myworkdayjobs.com/en-US/Magna"
    "?Worker_Type=5aaaed564f4301b1fdb0b8f1c6025ed1"
    "&Worker_Type=5aaaed564f43016878b0b7f1c60258d1"
    "&Country=bc33aa3152ec42d4995f4791a106ed09"
    "&Country=a30a87ed25634629aa6c3958aa2b91ea"
)


def _run(client, name: str, start_urls: list[str], *, details: bool, pages: int) -> dict:
    try:
        run = client.actor(ACTOR).call(
            run_input={
                "startUrls": [{"url": u} for u in start_urls],
                "pagination": pages,
                "details": details,
                "proxyUrls": {"useApifyProxy": True},
            },
            memory_mbytes=1024,
            run_timeout=timedelta(seconds=300),
            wait_duration=timedelta(seconds=320),
        )
        items = client.dataset(run.default_dataset_id).list_items(limit=100).items
        log = client.run(run.id).log().get() or ""
        first = items[0] if items else {}
        return {
            "status": str(run.status),
            "items": len(items),
            "usage_usd": float(run.usage_total_usd or 0),
            "fields": sorted(first.keys()),
            "sample": {
                k: (str(v)[:120] if k != "jobDescription" else len(str(v)))
                for k, v in first.items()
                if k not in ("videoInfo", "similarJobs")
            },
            "titles": [str(i.get("title"))[:60] for i in items[:15]],
            "log_tail": log[-900:],
        }
    except Exception as exc:  # noqa: BLE001 - diagnostic only
        return {"error": repr(exc)[:400]}


def main() -> None:
    token = os.environ.get("APIFY_TOKEN", "").strip()
    out: dict[str, object] = {}
    if not token:
        print("APIFY_TOKEN not set")
        return
    from apify_client import ApifyClient

    client = ApifyClient(token=token)
    out["facets"] = _run(client, "facets", [AIRBUS], details=False, pages=0)
    print("facets", json.dumps(out["facets"])[:600])
    out["details"] = _run(client, "details", [AIRBUS, MAGNA], details=True, pages=0)
    print("details", json.dumps(out["details"])[:600])
    job_url = None
    sample = out["details"].get("sample") if isinstance(out["details"], dict) else None
    if isinstance(sample, dict) and sample.get("url"):
        job_url = str(sample["url"]).replace("/wday/cxs/magna/", "/en-US/")
        job_url = job_url.replace("/wday/cxs/ag/", "/en-US/")
    if job_url:
        out["job_url"] = _run(client, "job_url", [job_url], details=True, pages=1)
        out["job_url"]["requested"] = job_url
        print("job_url", json.dumps(out["job_url"])[:600])
    Path("output").mkdir(exist_ok=True)
    Path("output/apify_probe.json").write_text(json.dumps(out, indent=2, default=str))


if __name__ == "__main__":
    main()
