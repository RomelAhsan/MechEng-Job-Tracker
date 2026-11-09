"""'Notifications' page (Phase 2).

Thin view for notification preferences and saved-filter subscriptions. Email
delivery itself is implemented in FEAT-005; this page persists the preferences
and subscriptions (via the service layer) that FEAT-005 consumes.
"""

from __future__ import annotations

import streamlit as st

from jobtracker.dashboard._shared import require_login, write_session_scope
from jobtracker.services import user_service


def render() -> None:
    user_id = require_login()
    st.title("Notifications")
    st.caption(
        "Choose how you want to be notified about new matching jobs. Email "
        "delivery is sent by the notifier when enabled."
    )

    with write_session_scope() as session:
        prefs = user_service.get_notification_prefs(session, user_id)

        st.subheader("Email preferences")
        with st.form("notif-prefs"):
            email_enabled = st.checkbox(
                "Enable email notifications",
                value=bool(prefs.get("email_enabled", False)),
            )
            email = st.text_input(
                "Notification email", value=prefs.get("email", "")
            )
            saved = st.form_submit_button("Save preferences")
        if saved:
            user_service.set_notification_prefs(
                session,
                user_id,
                {
                    "email_enabled": email_enabled,
                    "email": email,
                    "channel": "email",
                },
            )
            st.success("Preferences saved.")
            st.rerun()

        st.divider()
        st.subheader("Saved filters / subscriptions")
        st.caption(
            "Save a named filter to subscribe to its matching jobs. When notify "
            "is on, FEAT-005 emails you when new postings match."
        )

        with st.form("new-filter"):
            name = st.text_input("Filter name")
            country = st.text_input("Country contains (optional)")
            notify = st.checkbox("Notify me by email", value=True)
            create = st.form_submit_button("Create subscription")
        if create:
            try:
                filter_json: dict = {}
                if country.strip():
                    filter_json["countries"] = [country.strip()]
                user_service.create_saved_filter(
                    session,
                    user_id,
                    name,
                    filter_json,
                    notify=notify,
                    channel="email",
                )
                st.success("Subscription created.")
                st.rerun()
            except user_service.UserServiceError as exc:
                st.error(str(exc))

        for sub in user_service.list_saved_filters(session, user_id):
            cols = st.columns([4, 2, 1])
            cols[0].write(f"**{sub.name}** — {sub.filter_json or 'all jobs'}")
            cols[1].write("🔔 notify" if sub.notify else "muted")
            if cols[2].button("Delete", key=f"del-filter-{sub.id}"):
                user_service.delete_saved_filter(session, user_id, sub.id)
                st.rerun()
