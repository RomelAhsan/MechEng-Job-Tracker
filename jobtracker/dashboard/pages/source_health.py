"""'Source Health' page: check times, counts, errors, quarantine, accuracy.

Thin view over ``health_service`` (source-health rows + accuracy report) and
``dashboard_service.list_quarantine``. No business logic lives here.
"""

from __future__ import annotations

import streamlit as st

from jobtracker.dashboard._shared import session_scope
from jobtracker.services import dashboard_service, health_service


def _fmt(value) -> str:
    return value.strftime("%Y-%m-%d %H:%M") if value is not None else "—"


def render() -> None:
    st.title("Source Health")
    st.caption("Per-source check status, the accuracy report, and the quarantine queue.")

    with session_scope() as session:
        rows = health_service.source_health(session)
        report = health_service.accuracy_report(session)
        quarantine = dashboard_service.list_quarantine(session)

    st.subheader("Sources")
    if rows:
        st.dataframe(
            [
                {
                    "company": r.company_id,
                    "status": r.last_status or "—",
                    "last checked": _fmt(r.last_checked_at),
                    "last success": _fmt(r.last_successful_check_at),
                    "jobs": r.current_job_count,
                    "new (last run)": r.new_jobs_last_run,
                    "removed (last run)": r.removed_jobs_last_run,
                    "errors": r.error_count or 0,
                    "last error": r.last_error or "",
                }
                for r in rows
            ],
            use_container_width=True,
            hide_index=True,
        )
    else:
        st.info("No source-health records yet. Run an ingestion pass first.")

    st.divider()
    st.subheader("Accuracy report")
    cols = st.columns(3)
    cols[0].metric("Missing application URL", len(report.missing_application_url))
    cols[1].metric("Unknown term", len(report.unknown_term))
    cols[2].metric("Unknown sponsorship", len(report.unknown_sponsorship))
    cols = st.columns(3)
    cols[0].metric("Stale sources", len(report.stale_sources))
    cols[1].metric("Removed-but-present", len(report.removed_but_present))
    cols[2].metric("Active-but-absent", len(report.active_but_absent))
    st.caption(f"Total flagged issues: {report.total_issues()}")

    with st.expander("Accuracy report details"):
        st.write({
            "missing_application_url": report.missing_application_url,
            "unknown_term": report.unknown_term,
            "unknown_sponsorship": report.unknown_sponsorship,
            "stale_sources": report.stale_sources,
            "removed_but_present": report.removed_but_present,
            "active_but_absent": report.active_but_absent,
        })

    st.divider()
    st.subheader(f"Quarantine ({len(quarantine)})")
    st.caption("Payloads missing an application/source URL are quarantined, not dropped.")
    if quarantine:
        st.dataframe(
            [
                {
                    "id": q.id,
                    "company": q.company_id,
                    "source job id": q.source_job_id or "—",
                    "reason": q.reason or "",
                    "created": _fmt(q.created_at),
                }
                for q in quarantine
            ],
            use_container_width=True,
            hide_index=True,
        )
    else:
        st.info("Quarantine is empty.")
