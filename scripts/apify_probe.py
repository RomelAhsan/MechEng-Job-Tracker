"""Probe which Workday start-URL forms the Apify Workday Actor accepts.

Runs a few tiny Actor runs (1 page, no details) for one blocked board and
prints status, item count, and the tail of each run log. Costs about $0.001
per variant. Used from the manual discover-boards workflow.
"""

from __future__ import annotations

import json
import os
from datetime import timedelta
from pathlib import Path

from apify_client import ApifyClient

ACTOR = os.environ.get("APIFY_WORKDAY_ACTOR", "gooyer.co~myworkdayjobs").replace("~", "/")
BASE = "https://gevernova.wd5.myworkdayjobs.com"
SITE = "Vernova_ExternalSite"
# (url, proxy settings). Datacenter proxy got HTTP 521 from wd5 on the first
# probe, so try the residential group, plus a wd3 board as a control.
VARIANTS = {
    "wd5_residential": (
        f"{BASE}/en-US/{SITE}?q=intern",
        {"useApifyProxy": True, "apifyProxyGroups": ["RESIDENTIAL"]},
    ),
    "wd3_control": (
        "https://cae.wd3.myworkdayjobs.com/en-US/career?q=intern",
        {"useApifyProxy": True},
    ),
}


def main() -> None:
    token = os.environ.get("APIFY_TOKEN", "").strip()
    out: dict[str, object] = {}
    if not token:
        print("APIFY_TOKEN not set")
        return
    client = ApifyClient(token=token)
    for name, (url, proxy) in VARIANTS.items():
        try:
            run = client.actor(ACTOR).call(
                run_input={
                    "startUrls": [{"url": url}],
                    "pagination": 1,
                    "details": False,
                    "proxyUrls": proxy,
                },
                memory_mbytes=1024,
                run_timeout=timedelta(seconds=180),
                wait_duration=timedelta(seconds=200),
            )
            run_id = run.id
            items = client.dataset(run.default_dataset_id).list_items(limit=3).items
            log = client.run(run_id).log().get() or ""
            out[name] = {
                "url": url,
                "status": str(run.status),
                "usage_usd": float(run.usage_total_usd or 0),
                "items": len(items),
                "first": items[0] if items else None,
                "log_tail": log[-1500:],
            }
        except Exception as exc:  # noqa: BLE001
            out[name] = {"url": url, "error": repr(exc)}
        print(name, json.dumps(out[name])[:400])
    Path("output").mkdir(exist_ok=True)
    Path("output/apify_probe.json").write_text(json.dumps(out, indent=2, default=str))


if __name__ == "__main__":
    main()
