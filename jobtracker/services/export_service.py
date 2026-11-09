"""Export service (design 10): markdown / json / csv exports.

Produces the SPEC export fields (company, title, location, term & duration,
citizenship/sponsorship status, posted date, application URL, fit score, full
description, requirements, preferred qualifications, skills) plus a user
profile summary, writing the artifact to ``output/``.

All formatting logic lives here so callers (CLI, dashboard) stay thin.
"""

from __future__ import annotations

import csv
import io
import json
import logging
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from jobtracker.config.user_loader import load_user_profile
from jobtracker.db.models import Company, Job
from jobtracker.schemas.user_profile import UserProfile
from jobtracker.settings import get_settings

logger = logging.getLogger("jobtracker.services.export")

#: SPEC export columns in order (keys of the per-job dict assembled below).
EXPORT_FIELDS: tuple[str, ...] = (
    "company",
    "title",
    "location",
    "term_season",
    "duration",
    "citizenship_requirement",
    "sponsorship_status",
    "posted_date",
    "application_url",
    "fit_score",
    "description",
    "requirements",
    "preferred_qualifications",
    "skills",
)

_FORMATS = ("markdown", "json", "csv")


def _fmt_dt(value: datetime | None) -> str:
    return value.isoformat() if value is not None else ""


def _job_row(job: Job, company_name: str) -> dict[str, object]:
    duration = job.duration_label or (
        f"{job.duration_months} months" if job.duration_months else ""
    )
    return {
        "company": company_name,
        "title": job.title or "",
        "location": job.location or "",
        "term_season": job.term_season or "unknown",
        "duration": duration,
        "citizenship_requirement": job.citizenship_requirement or "",
        "sponsorship_status": job.visa_sponsorship or "unknown",
        "posted_date": _fmt_dt(job.posted_at),
        "application_url": job.application_url or "",
        "fit_score": job.fit_score if job.fit_score is not None else "",
        "description": job.description or "",
        "requirements": list(job.requirements or []),
        "preferred_qualifications": list(job.preferred_qualifications or []),
        "skills": list(job.skills or []),
    }


def _profile_summary(profile: UserProfile) -> dict[str, object]:
    return {
        "name": profile.name,
        "program": profile.program,
        "university": profile.university,
        "citizenship": list(profile.citizenship),
        "work_authorization": list(profile.work_authorization),
        "available_terms": list(profile.available_terms),
        "preferred_duration_months": profile.preferred_duration_months,
        "interests": list(profile.interests),
        "skills": list(profile.skills),
    }


def collect_jobs(session: Session) -> list[dict[str, object]]:
    """Collect active job rows joined to their company name for export."""
    stmt = (
        select(Job, Company.name)
        .join(Company, Company.id == Job.company_id)
        .where(Job.status == "active")
    )
    rows: list[dict[str, object]] = []
    for job, company_name in session.execute(stmt):
        rows.append(_job_row(job, company_name))
    return rows


def _to_markdown(rows: list[dict[str, object]], profile: UserProfile) -> str:
    summary = _profile_summary(profile)
    lines = ["# Job Export", "", "## User Profile", ""]
    for key, value in summary.items():
        if isinstance(value, list):
            value = ", ".join(str(v) for v in value)
        lines.append(f"- **{key}**: {value}")
    lines.extend(["", "## Jobs", ""])
    for row in rows:
        lines.append(f"### {row['title']} — {row['company']}")
        lines.append(f"- **Location**: {row['location']}")
        lines.append(f"- **Term**: {row['term_season']}")
        lines.append(f"- **Duration**: {row['duration']}")
        lines.append(f"- **Citizenship**: {row['citizenship_requirement']}")
        lines.append(f"- **Sponsorship**: {row['sponsorship_status']}")
        lines.append(f"- **Posted**: {row['posted_date']}")
        lines.append(f"- **Fit score**: {row['fit_score']}")
        lines.append(f"- **Apply**: {row['application_url']}")
        lines.append(f"- **Skills**: {', '.join(row['skills'])}")  # type: ignore[arg-type]
        lines.append("")
        lines.append("**Requirements**")
        for item in row["requirements"]:  # type: ignore[union-attr]
            lines.append(f"- {item}")
        lines.append("")
        lines.append("**Preferred qualifications**")
        for item in row["preferred_qualifications"]:  # type: ignore[union-attr]
            lines.append(f"- {item}")
        lines.append("")
        lines.append("**Description**")
        lines.append(str(row["description"]))
        lines.append("")
    return "\n".join(lines)


def _to_json(rows: list[dict[str, object]], profile: UserProfile) -> str:
    payload = {"user_profile": _profile_summary(profile), "jobs": rows}
    return json.dumps(payload, indent=2, ensure_ascii=False)


def _to_csv(rows: list[dict[str, object]], profile: UserProfile) -> str:
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    # Prefix the user-profile summary as a leading comment-style header row so a
    # single CSV artifact still carries the profile context.
    writer.writerow(["# user_profile", json.dumps(_profile_summary(profile))])
    writer.writerow(EXPORT_FIELDS)
    for row in rows:
        writer.writerow(
            [
                "; ".join(str(v) for v in row[field])  # type: ignore[union-attr]
                if isinstance(row[field], list)
                else row[field]
                for field in EXPORT_FIELDS
            ]
        )
    return buffer.getvalue()


_RENDERERS = {
    "markdown": _to_markdown,
    "json": _to_json,
    "csv": _to_csv,
}

_EXTENSIONS = {"markdown": "md", "json": "json", "csv": "csv"}


def render(
    session: Session, fmt: str, *, profile: UserProfile | None = None
) -> str:
    """Render all active jobs to the given format and return the text body."""
    if fmt not in _FORMATS:
        raise ValueError(f"Unknown export format '{fmt}'; choose one of {_FORMATS}.")
    active_profile = profile or load_user_profile(get_settings().user_file)
    rows = collect_jobs(session)
    return _RENDERERS[fmt](rows, active_profile)


def _collect_rows_for_job_ids(session: Session, job_ids: list[str]) -> list[dict[str, object]]:
    """Collect export rows for an explicit list of job ids (any status)."""
    if not job_ids:
        return []
    stmt = (
        select(Job, Company.name)
        .join(Company, Company.id == Job.company_id)
        .where(Job.job_id.in_(job_ids))
    )
    by_id = {job.job_id: _job_row(job, name) for job, name in session.execute(stmt)}
    # Preserve the caller's ordering.
    return [by_id[jid] for jid in job_ids if jid in by_id]


def collect_ai_export_rows(
    session: Session,
    *,
    job_ids: list[str] | None = None,
    filters: object | None = None,
    user_id: int | None = None,
    saved_only: bool = False,
) -> list[dict[str, object]]:
    """Collect export rows for the export-for-external-AI feature (SPEC).

    Selection modes (mutually exclusive, evaluated in priority order):

    * ``job_ids`` - one or many specific jobs,
    * ``saved_only`` + ``user_id`` - all of the user's saved jobs,
    * ``filters`` - all jobs matching a :class:`JobFilters` (dashboard filters),
    * otherwise - all active jobs.

    The rows carry every SPEC export field (company, title, location, term +
    duration, citizenship/sponsorship, posted date, application URL, fit score,
    full cleaned description, requirements, preferred qualifications, skills) so
    the output is clean enough to paste into an external AI.
    """
    # Imported here to avoid a module-load import cycle with dashboard_service.
    from jobtracker.services import dashboard_service

    if job_ids:
        return _collect_rows_for_job_ids(session, job_ids)

    if saved_only and user_id is not None:
        views = dashboard_service.list_saved_job_views(session, user_id)
        return _collect_rows_for_job_ids(session, [v.job_id for v in views])

    if filters is not None:
        views = dashboard_service.list_jobs(session, filters, user_id=user_id)
        return _collect_rows_for_job_ids(session, [v.job_id for v in views])

    return collect_jobs(session)


def render_ai_export(
    session: Session,
    fmt: str,
    *,
    profile: UserProfile | None = None,
    job_ids: list[str] | None = None,
    filters: object | None = None,
    user_id: int | None = None,
    saved_only: bool = False,
) -> str:
    """Render an export-for-external-AI artifact body for the selected jobs."""
    if fmt not in _FORMATS:
        raise ValueError(f"Unknown export format '{fmt}'; choose one of {_FORMATS}.")
    active_profile = profile or load_user_profile(get_settings().user_file)
    rows = collect_ai_export_rows(
        session,
        job_ids=job_ids,
        filters=filters,
        user_id=user_id,
        saved_only=saved_only,
    )
    return _RENDERERS[fmt](rows, active_profile)


def export(
    session: Session,
    fmt: str = "markdown",
    *,
    output: str | None = None,
    profile: UserProfile | None = None,
) -> Path:
    """Render and write an export artifact to ``output/`` (default name)."""
    body = render(session, fmt, profile=profile)

    if output is not None:
        out_path = Path(output)
    else:
        stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
        out_path = Path("output") / f"jobs-{stamp}.{_EXTENSIONS[fmt]}"

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(body, encoding="utf-8")
    logger.info("Exported %s jobs to %s", fmt, out_path)
    return out_path
