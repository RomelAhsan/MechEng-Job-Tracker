# ruff: noqa: E501  (long lookup tables)
"""Find public Workday boards and check Workday job links (run from CI).

Some Workday data centers (wd1, wd5, ...) refuse connections from the build
sandbox but answer GitHub's runners, so this runs as a manual GitHub Actions
job and writes ``output/discovery.json``:

* ``boards``: for each candidate employer, every ``tenant.dc/site`` whose
  public CXS endpoint answers 200, with its posting count.
* ``links``: for every Workday apply link on the live site, whether the job
  still exists (the CXS detail endpoint) and what the public page returns.

Usage: python scripts/discover_boards.py
"""

from __future__ import annotations

import concurrent.futures as cf
import json
import re
from pathlib import Path

import httpx

HEADERS = {
    "Content-Type": "application/json",
    "Accept": "application/json",
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
}
BODY = {"limit": 1, "offset": 0, "appliedFacets": {}, "searchText": ""}
DCS = ["wd1", "wd3", "wd5", "wd10", "wd12", "wd103", "wd108", "wd501"]
FEED = "https://RomelAhsan.github.io/MechEng-Job-Tracker/data/jobs.json"

# employer -> tenant name guesses
CANDIDATES: dict[str, list[str]] = {
    "wsp": ["wsp", "wspgroup"], "stantec": ["stantec"], "atkinsrealis": ["atkinsrealis", "snclavalin"],
    "aecom": ["aecom"], "tetratech": ["tetratech"], "ghd": ["ghd"], "jacobs": ["jacobs", "jacobsengineering"],
    "fluor": ["fluor"], "worley": ["worley"], "wood": ["woodplc", "wood"], "mottmac": ["mottmac"],
    "arup": ["arup"], "honeywell": ["honeywell"], "l3harris": ["l3harris"], "gdms": ["gdms", "gdit"],
    "collins": ["rtx", "collins"], "pwc": ["rtx", "prattwhitney"], "lockheed": ["lmco"],
    "northrop": ["ngc", "northropgrumman"], "bae": ["baesystems", "bae"], "linamar": ["linamar"],
    "martinrea": ["martinrea"], "magna": ["magna"], "hydroquebec": ["hydroquebec"],
    "bchydro": ["bchydro"], "suncor": ["suncor"], "cnrl": ["cnrl"], "imperialoil": ["exxonmobil"],
    "cameco": ["cameco"], "vale": ["vale"], "siemens": ["siemens"], "siemensenergy": ["siemensenergy"],
    "abb": ["abb"], "schneider": ["schneiderelectric", "se"], "emerson": ["emerson"],
    "hitachienergy": ["hitachienergy", "hitachi"], "ge": ["geaerospace", "ge"], "toyota": ["toyota", "tmna"],
    "honda": ["honda"], "gm": ["generalmotors", "gm"], "ford": ["ford"], "stellantis": ["stellantis"],
    "cummins": ["cummins"], "caterpillar": ["cat", "caterpillar"], "deere": ["johndeere", "deere"],
    "tesla": ["tesla"], "intel": ["intel"], "amat": ["amat"], "lam": ["lamresearch"],
    "ti": ["ti", "texasinstruments"], "qualcomm": ["qualcomm"], "intuitive": ["intuitive"],
    "chevron": ["chevron"], "bakerhughes": ["bakerhughes"], "slb": ["slb"], "halliburton": ["halliburton"],
    "safran": ["safran"], "standardaero": ["standardaero"], "textron": ["textron"],
    "dehavilland": ["dehavilland"], "mda": ["mda", "mdaspace"], "asml": ["asml"], "kinova": ["kinova"],
}
SITE_GUESSES = [
    "External", "external", "Careers", "careers", "Career", "career", "Search", "Jobs", "jobs",
    "ExternalCareers", "External_Careers", "ExternalCareerSite", "CareerSite", "Global",
    "GlobalCareers", "Experienced", "Campus", "Students", "EarlyCareers",
]


def _sites(tenant: str) -> list[str]:
    cap = tenant.capitalize()
    extra = [tenant, cap, tenant.upper(), f"{cap}Careers", f"{cap}_Careers", f"{tenant}_careers",
             f"{cap}External", f"{cap}_External", f"{cap}ExternalCareers", f"{cap}ExternalCareerSite",
             f"{tenant}careers", f"{cap}Jobs", f"External_{cap}", f"{cap}_External_Careers"]
    seen: list[str] = []
    for site in SITE_GUESSES + extra:
        if site not in seen:
            seen.append(site)
    return seen


def _post(client: httpx.Client, tenant: str, dc: str, site: str) -> tuple[int | None, int | None]:
    url = f"https://{tenant}.{dc}.myworkdayjobs.com/wday/cxs/{tenant}/{site}/jobs"
    try:
        r = client.post(url, json=BODY, headers=HEADERS, timeout=15)
        total = r.json().get("total") if r.status_code == 200 else None
        return r.status_code, total
    except Exception:  # noqa: BLE001
        return None, None


def discover() -> dict:
    client = httpx.Client()
    probes = [(k, t, dc) for k, ts in CANDIDATES.items() for t in ts for dc in DCS]
    with cf.ThreadPoolExecutor(32) as ex:
        codes = list(ex.map(lambda p: _post(client, p[1], p[2], "zz_probe")[0], probes))
    live = [p for p, code in zip(probes, codes, strict=True) if code in (200, 404)]
    tasks = [(k, t, dc, s) for k, t, dc in live for s in _sites(t)]
    with cf.ThreadPoolExecutor(32) as ex:
        results = list(ex.map(lambda x: _post(client, x[1], x[2], x[3]), tasks))
    boards: dict[str, list[dict]] = {}
    for (k, t, dc, s), (code, total) in zip(tasks, results, strict=True):
        if code == 200:
            boards.setdefault(k, []).append({"tenant": t, "dc": dc, "site": s, "total": total})
    return {"tenants_found": [list(p) for p in live], "boards": boards}


_WD_LINK = re.compile(
    r"https://(?P<t>[a-z0-9-]+)\.(?P<dc>wd\d+)\.myworkdayjobs\.com/(?:[a-z]{2}-[A-Z]{2}/)?"
    r"(?P<site>[^/]+)(?P<path>/job/.+)$"
)


def check_links() -> list[dict]:
    client = httpx.Client(follow_redirects=True)
    try:
        jobs = client.get(FEED, timeout=30).json().get("jobs", [])
    except Exception:  # noqa: BLE001
        return []
    out = []
    for job in jobs:
        url = job.get("application_url") or ""
        m = _WD_LINK.match(url)
        if not m:
            continue
        detail = f"https://{m['t']}.{m['dc']}.myworkdayjobs.com/wday/cxs/{m['t']}/{m['site']}{m['path']}"
        try:
            d = client.get(detail, headers=HEADERS, timeout=20).status_code
        except Exception:  # noqa: BLE001
            d = None
        try:
            page = client.get(url, headers={"User-Agent": HEADERS["User-Agent"]}, timeout=20)
            p = page.status_code
        except Exception:  # noqa: BLE001
            p = None
        out.append({"title": job.get("title"), "url": url, "detail_status": d, "page_status": p})
    return out


if __name__ == "__main__":
    report = {"discovery": discover(), "links": check_links()}
    Path("output").mkdir(exist_ok=True)
    Path("output/discovery.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    boards = report["discovery"]["boards"]
    print(f"Workday boards found for {len(boards)} employers")
    for k, v in sorted(boards.items()):
        print(" ", k, [(b["tenant"], b["dc"], b["site"], b["total"]) for b in v])
    bad = [x for x in report["links"] if x["detail_status"] != 200]
    print(f"Workday links checked: {len(report['links'])}, not found: {len(bad)}")
    for x in bad:
        print("  BROKEN", x["detail_status"], x["page_status"], x["url"][:120])
