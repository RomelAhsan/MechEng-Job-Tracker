"""'Notifications' page (Phase 2 + FEAT-005).

Thin view for notification preferences, saved-filter subscriptions, and the
delivery history. All logic lives in ``user_service`` and
``notification_service``; this page only collects input and renders results.
"""

from __future__ import annotations

import streamlit as st

from jobtracker.dashboard._shared import _fmt_dt_time, require_login, write_session_scope
from jobtracker.services import notification_service, user_service


def render() -> None:
    user_id = require_login()
    st.title("Notifications")
    st.caption(
        "Choose how you want to be notified about new matching jobs. When no "
        "SMTP credentials are configured, emails are written to the outbox so "
        "nothing ever breaks."
    )

    with write_session_scope() as session:
        prefs = user_service.get_notification_prefs(session, user_id)

        st.subheader("Email preferences")
        with st.form("notif-prefs"):
            email_enabled = st.checkbox(
                "Enable email notifications",
                value=bool(prefs.get("email_enabled", True)),
            )
            email = st.text_input(
                "Notification email", value=prefs.get("email", "")
            )
            preferred_locations = st.text_input(
                "Preferred locations (comma-separated, optional)",
                value=", ".join(prefs.get("preferred_locations", [])),
                help="You get a notification when a new job matches one of these.",
            )
            saved = st.form_submit_button("Save preferences")
        if saved:
            locs = [x.strip() for x in preferred_locations.split(",") if x.strip()]
            user_service.set_notification_prefs(
                session,
                user_id,
                {
                    "email_enabled": email_enabled,
                    "email": email,
                    "channel": "email",
                    "preferred_locations": locs,
                },
            )
            st.success("Preferences saved.")
            st.rerun()

        st.divider()
        st.subheader("Subscribe to these results / Email me new matches")
        st.caption(
            "Save a named filter to subscribe to its matching jobs. When notify "
            "is on, you get an email whenever a new posting matches. Use 'Email "
            "me jobs on my page' to subscribe to your Relevant-For-Me results."
        )

        # Subscribe from the user's current "Relevant For Me" page filters, if
        # the Relevant page stashed them in session state.
        page_filters = st.session_state.get("relevant_filter_json")
        page_cols = st.columns([3, 2])
        page_name = page_cols[0].text_input(
            "Name for your page subscription",
            value="Jobs on my page",
            key="page-sub-name",
        )
        if page_cols[1].button("Email me jobs on my page"):
            try:
                user_service.create_saved_filter(
                    session,
                    user_id,
                    page_name,
                    page_filters or {"classifications": ["strong_match", "possible_match"]},
                    notify=True,
                    channel="email",
                )
                st.success("Subscribed to your page's matching jobs.")
                st.rerun()
            except user_service.UserServiceError as exc:
                st.error(str(exc))

        with st.form("new-filter"):
            name = st.text_input("Filter name")
            country = st.text_input("Country contains (optional)")
            classification = st.multiselect(
                "Classifications (optional)",
                options=["strong_match", "possible_match", "weak_match"],
                default=["strong_match"],
            )
            notify = st.checkbox("Notify me by email", value=True)
            create = st.form_submit_button("Create subscription")
        if create:
            try:
                filter_json: dict = {}
                if country.strip():
                    filter_json["countries"] = [country.strip()]
                if classification:
                    filter_json["classifications"] = classification
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

        st.divider()
        st.subheader("Your subscriptions")
        for sub in user_service.list_saved_filters(session, user_id):
            cols = st.columns([4, 2, 2])
            cols[0].write(f"**{sub.name}** — {sub.filter_json or 'all jobs'}")
            toggle_label = "Disable" if sub.notify else "Enable"
            if cols[1].button(toggle_label, key=f"toggle-filter-{sub.id}"):
                user_service.update_saved_filter(
                    session, user_id, sub.id, notify=not sub.notify
                )
                st.rerun()
            if cols[2].button("Delete", key=f"del-filter-{sub.id}"):
                user_service.delete_saved_filter(session, user_id, sub.id)
                st.rerun()

        st.divider()
        st.subheader("Notification history")
        history = notification_service.notify_history(session, user_id)
        if not history:
            st.caption("No notifications sent yet.")
        else:
            for entry in history:
                st.write(
                    f"{_fmt_dt_time(entry.sent_at)} · **{entry.rule}** · "
                    f"{entry.subject or ''} → {entry.to_email or '—'} "
                    f"({entry.status})"
                )
