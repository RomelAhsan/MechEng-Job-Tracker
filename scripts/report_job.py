"""Add or remove a job in config/reported_jobs.json (used by report-job.yml).

Reads JOB_ID, ACTION ("hide" or "restore"), TITLE and COMPANY from the
environment. JOB_ID must look like a feed job_id; TITLE/COMPANY are trimmed
display text only. Standard library only, so the workflow needs no install.
"""

from __future__ import annotations

import json
import os
import re
import sys
from datetime import UTC, datetime
from pathlib import Path

PATH = Path("config/reported_jobs.json")
_JOB_ID_RE = re.compile(r"^[A-Za-z0-9-]{1,64}$")


def _clean(value: str, limit: int = 200) -> str:
    return " ".join((value or "").split())[:limit]


def main() -> int:
    job_id = (os.environ.get("JOB_ID") or "").strip()
    action = (os.environ.get("ACTION") or "hide").strip().lower()
    if not _JOB_ID_RE.match(job_id):
        print(f"Invalid job_id: {job_id!r}", file=sys.stderr)
        return 2
    if action not in ("hide", "restore"):
        print(f"Invalid action: {action!r}", file=sys.stderr)
        return 2

    data = {"jobs": []}
    if PATH.exists():
        data = json.loads(PATH.read_text(encoding="utf-8"))
    jobs = [j for j in data.get("jobs", []) if isinstance(j, dict)]
    jobs = [j for j in jobs if j.get("job_id") != job_id]
    if action == "hide":
        jobs.append(
            {
                "job_id": job_id,
                "title": _clean(os.environ.get("TITLE", "")),
                "company": _clean(os.environ.get("COMPANY", "")),
                "reported_at": datetime.now(UTC).isoformat(timespec="seconds"),
            }
        )
    payload = {
        "about": (
            "Jobs you reported as dead links on the website. They are not shown. "
            "Use Restore in the Hidden jobs tab to bring one back."
        ),
        "jobs": sorted(jobs, key=lambda j: j.get("reported_at", "")),
    }
    PATH.parent.mkdir(parents=True, exist_ok=True)
    PATH.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"{action}: {job_id} ({len(jobs)} reported jobs)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
