"""'Companies' page: table + add-by-URL + edit/enable/disable/remove.

Thin view: the table and source-health summary come from
``dashboard_service.list_company_views``; all writes go through
``company_service`` (add_from_url / enable / disable / remove / update_settings).
No business logic lives in this page.
"""

from __future__ import annotations

import streamlit as st

from jobtracker.dashboard._shared import session_scope, write_session_scope
from jobtracker.services import company_service, dashboard_service

_PRIORITIES = ("high", "medium", "low")


def _add_company(url: str, company_id: str, name: str, priority: str) -> str:
    from jobtracker.connectors.http import HttpClient

    with write_session_scope() as session, HttpClient() as http:
        result = company_service.add_from_url(
            session,
            url,
            http,
            company_id=company_id or None,
            name=name or None,
            priority=priority,
        )
    return (
        f"Added {result.company_id} (ats={result.ats}, token={result.board_token}); "
        f"baseline imported {result.baseline_jobs} jobs."
    )


def render() -> None:
    st.title("Companies")
    st.caption("Tracked employers, their ATS source health, and controls.")

    with st.form("add-company"):
        st.subheader("Add a company by careers URL")
        url = st.text_input("Careers URL")
        cols = st.columns(3)
        company_id = cols[0].text_input("Explicit id (optional)")
        name = cols[1].text_input("Display name (optional)")
        priority = cols[2].selectbox("Priority", _PRIORITIES, index=1)
        submitted = st.form_submit_button("Add company")
    if submitted and url:
        try:
            st.success(_add_company(url, company_id, name, priority))
        except Exception as exc:  # noqa: BLE001 - surface add errors to the user.
            st.error(f"Could not add company: {exc}")

    st.divider()
    st.subheader("Tracked companies")

    with session_scope() as session:
        companies = dashboard_service.list_company_views(session)

    if not companies:
        st.info("No companies tracked yet. Add one above.")
        return

    st.dataframe(
        [
            {
                "id": c.id,
                "name": c.name,
                "ats": c.ats,
                "priority": c.priority,
                "enabled": c.enabled,
                "active": c.active,
                "active jobs": c.active_job_count,
                "health": c.last_status or "—",
                "errors": c.error_count,
                "interval (min)": c.check_interval_minutes,
            }
            for c in companies
        ],
        use_container_width=True,
        hide_index=True,
    )

    st.subheader("Edit a company")
    ids = [c.id for c in companies]
    selected = st.selectbox("Company", ids)
    current = next(c for c in companies if c.id == selected)

    cols = st.columns(2)
    new_priority = cols[0].selectbox(
        "Priority",
        _PRIORITIES,
        index=_PRIORITIES.index(current.priority) if current.priority in _PRIORITIES else 1,
        key="edit-priority",
    )
    new_interval = cols[1].number_input(
        "Check interval (minutes)",
        min_value=0,
        value=int(current.check_interval_minutes or 0),
        key="edit-interval",
    )

    action_cols = st.columns(4)
    if action_cols[0].button("Save settings"):
        with write_session_scope() as session:
            company_service.update_settings(
                session,
                selected,
                priority=new_priority,
                check_interval_minutes=int(new_interval) or None,
            )
        st.success(f"Updated {selected}.")
    if action_cols[1].button("Enable"):
        with write_session_scope() as session:
            company_service.enable(session, selected)
        st.success(f"Enabled {selected}.")
    if action_cols[2].button("Disable"):
        with write_session_scope() as session:
            company_service.disable(session, selected)
        st.success(f"Disabled {selected}.")
    if action_cols[3].button("Remove"):
        with write_session_scope() as session:
            company_service.remove(session, selected)
        st.success(f"Removed {selected} (history retained).")
