"""'Companies' page: table + add-by-URL + edit/enable/disable/remove.

Thin view: the table and source-health summary come from
``dashboard_service.list_company_views``; all writes go through
``company_service`` (add_from_url / enable / disable / remove / update_settings).
No business logic lives in this page.
"""

from __future__ import annotations

import streamlit as st

from jobtracker.dashboard._shared import (
    is_admin,
    require_login,
    session_scope,
    write_session_scope,
)
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


def _add_company_by_name(name: str, priority: str, *, confirm: bool = False):
    from jobtracker.connectors.http import HttpClient

    with write_session_scope() as session, HttpClient() as http:
        return company_service.add_from_name(
            session, name, http, priority=priority, confirm=confirm
        )


def render() -> None:
    require_login()
    admin = is_admin()
    st.title("Companies")
    st.caption("Tracked employers, their ATS source health, and controls.")

    if not admin:
        st.info(
            "The company registry is shared. Only an admin can add, edit, or "
            "remove companies; you can view all tracked companies below."
        )

    if not admin:
        _render_company_table()
        return

    with st.form("add-company-by-name"):
        st.subheader("Add a company by name")
        st.caption(
            "Type a company name (e.g. 'Toyota'). We probe public job boards "
            "(Greenhouse / Lever / Ashby / Workable) and import the first match. "
            "If it cannot be auto-resolved, use the careers-URL form below."
        )
        name_cols = st.columns([3, 1])
        by_name = name_cols[0].text_input("Company name", key="add-by-name-input")
        name_priority = name_cols[1].selectbox(
            "Priority", _PRIORITIES, index=1, key="add-by-name-priority"
        )
        name_submitted = st.form_submit_button("Resolve and add")
    if name_submitted and by_name:
        with st.spinner(f"Searching public boards for '{by_name}'..."):
            result = _add_company_by_name(by_name, name_priority)
        if result.resolved:
            st.success(result.message)
        elif result.needs_verification:
            # A board was found but its identity could not be confirmed; make
            # the user explicitly confirm before importing a possibly-wrong org.
            st.session_state["pending_name_verify"] = {
                "name": by_name,
                "priority": name_priority,
                "url": result.candidate_url,
                "message": result.message,
            }
        else:
            st.warning(result.message)

    pending = st.session_state.get("pending_name_verify")
    if pending:
        st.warning(pending["message"])
        verify_cols = st.columns(2)
        if verify_cols[0].button(
            f"Yes, import {pending['url']}", key="confirm-name-add"
        ):
            with st.spinner("Importing confirmed board..."):
                confirmed = _add_company_by_name(
                    pending["name"], pending["priority"], confirm=True
                )
            st.session_state.pop("pending_name_verify", None)
            if confirmed.resolved:
                st.success(confirmed.message)
            else:
                st.warning(confirmed.message)
        if verify_cols[1].button("Cancel", key="cancel-name-add"):
            st.session_state.pop("pending_name_verify", None)

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

    _render_company_table(with_edit=True)


def _render_company_table(*, with_edit: bool = False) -> None:
    import streamlit as st

    st.divider()
    st.subheader("Tracked companies")

    with session_scope() as session:
        companies = dashboard_service.list_company_views(session)

    if not companies:
        st.info("No companies tracked yet.")
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

    if not with_edit:
        return

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
