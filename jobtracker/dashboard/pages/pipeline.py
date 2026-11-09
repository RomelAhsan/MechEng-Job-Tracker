"""'Saved / Application Pipeline' page (Phase 2).

Thin view over the saved-job + application-pipeline services. Shows the user's
hearted jobs with a status dropdown, notes, and a deadline, plus their
application status. All writes go through the service layer.
"""

from __future__ import annotations

from datetime import UTC, datetime, time

import streamlit as st

from jobtracker.dashboard._shared import render_job_card, require_login, write_session_scope
from jobtracker.services import dashboard_service, saved_job_service
from jobtracker.services.saved_job_service import PIPELINE_STATUSES


def render() -> None:
    user_id = require_login()
    st.title("Saved / Application Pipeline")
    st.caption(
        "Jobs you have hearted. Track each application's status, add notes, and "
        "set a deadline. Resume drafts arrive in a later phase."
    )

    with write_session_scope() as session:
        views = dashboard_service.list_saved_job_views(session, user_id)
        statuses = {
            v.job_id: saved_job_service.get_status(session, user_id, v.job_id)
            for v in views
        }

        if not views:
            st.info("No saved jobs yet. Heart a job from any listing to add it here.")
            return

        st.write(f"**{len(views)}** saved postings.")
        for view in views:
            current = statuses.get(view.job_id)
            current_status = current.status if current else "saved"
            current_note = current.note if current else ""
            current_deadline = current.deadline if current else None

            with st.expander(
                f"{view.title} — {view.company_name}  ·  {current_status}",
                expanded=False,
            ):
                render_job_card(view, user_id=user_id)

                with st.form(f"pipeline-{view.job_id}"):
                    status = st.selectbox(
                        "Application status",
                        options=list(PIPELINE_STATUSES),
                        index=_status_index(current_status),
                        key=f"status-{view.job_id}",
                    )
                    note = st.text_area(
                        "Notes", value=current_note or "", key=f"note-{view.job_id}"
                    )
                    set_deadline = st.checkbox(
                        "Set deadline",
                        value=current_deadline is not None,
                        key=f"has-deadline-{view.job_id}",
                    )
                    deadline_date = st.date_input(
                        "Deadline",
                        value=(current_deadline.date() if current_deadline else None),
                        key=f"deadline-{view.job_id}",
                    )
                    saved = st.form_submit_button("Save pipeline entry")

                if saved:
                    deadline_value = None
                    if set_deadline and deadline_date is not None:
                        deadline_value = datetime.combine(
                            deadline_date, time.min, tzinfo=UTC
                        )
                    saved_job_service.set_status(
                        session,
                        user_id,
                        view.job_id,
                        status,
                        note=note,
                        deadline=deadline_value,
                    )
                    st.success("Saved.")
                    st.rerun()

                if st.button("Remove from saved", key=f"remove-{view.job_id}"):
                    saved_job_service.unsave_job(session, user_id, view.job_id)
                    st.rerun()


def _status_index(status: str | None) -> int:
    try:
        return list(PIPELINE_STATUSES).index(status or "saved")
    except ValueError:
        return 0
