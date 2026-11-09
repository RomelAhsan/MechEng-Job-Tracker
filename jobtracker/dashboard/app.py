"""Streamlit dashboard entry point (design 11).

Phase 2 adds an email/password login gate before the navigation is shown. The
authenticated user id is kept in ``st.session_state`` and every page is a thin
view that calls the service layer only; this module wires the pages together
and holds no business logic. Launch via ``python -m jobtracker dashboard`` which
shells out to ``streamlit run jobtracker/dashboard/app.py``.
"""

from __future__ import annotations

import streamlit as st

from jobtracker.dashboard._shared import seed_profile_data, write_session_scope
from jobtracker.dashboard.pages import (
    all_internships,
    companies,
    notifications,
    pipeline,
    profile,
    relevant,
    source_health,
)
from jobtracker.services import auth_service


def _render_login_gate() -> None:
    """Render the email/password login + registration forms (no business logic)."""
    st.title("MechEng Co-op Job Tracker")
    st.caption("Sign in to track jobs, save favourites, and manage your pipeline.")

    with write_session_scope() as session:
        has_users = auth_service.user_count(session) > 0

    login_tab, register_tab = st.tabs(["Sign in", "Create account"])

    with login_tab:
        with st.form("login-form"):
            email = st.text_input("Email", key="login-email")
            password = st.text_input("Password", type="password", key="login-password")
            submitted = st.form_submit_button("Sign in")
        if submitted:
            with write_session_scope() as session:
                user = auth_service.authenticate(session, email, password)
                if user is None:
                    st.error("Invalid email or password.")
                else:
                    st.session_state["user_id"] = user.id
                    st.session_state["user_email"] = user.email
                    st.session_state["user_role"] = user.role
                    st.rerun()

    with register_tab:
        if not has_users:
            st.info("The first account you create becomes the admin.")
        with st.form("register-form"):
            display_name = st.text_input("Display name", key="register-name")
            email_r = st.text_input("Email", key="register-email")
            password_r = st.text_input("Password", type="password", key="register-password")
            submitted_r = st.form_submit_button("Create account")
        if submitted_r:
            try:
                with write_session_scope() as session:
                    user = auth_service.register(
                        session,
                        email_r,
                        password_r,
                        display_name=display_name or None,
                        seed_profile=seed_profile_data(),
                    )
                    st.session_state["user_id"] = user.id
                    st.session_state["user_email"] = user.email
                    st.session_state["user_role"] = user.role
                st.rerun()
            except auth_service.AuthError as exc:
                st.error(str(exc))


def _render_sidebar_account() -> None:
    """Show the current user and a logout control in the sidebar."""
    with st.sidebar:
        st.markdown(f"**{st.session_state.get('user_email', '')}**")
        st.caption(f"Role: {st.session_state.get('user_role', 'member')}")
        if st.button("Log out", key="logout"):
            for key in ("user_id", "user_email", "user_role"):
                st.session_state.pop(key, None)
            st.rerun()


def main() -> None:
    st.set_page_config(page_title="MechEng Co-op Job Tracker", layout="wide")

    if st.session_state.get("user_id") is None:
        _render_login_gate()
        return

    _render_sidebar_account()

    pages = [
        st.Page(relevant.render, title="Relevant For Me", default=True),
        st.Page(all_internships.render, title="All Internships"),
        st.Page(pipeline.render, title="Saved / Pipeline"),
        st.Page(companies.render, title="Companies"),
        st.Page(source_health.render, title="Source Health"),
        st.Page(profile.render, title="My Profile"),
        st.Page(notifications.render, title="Notifications"),
    ]
    st.navigation(pages).run()


# Streamlit executes the script top to bottom; call main on import/run.
main()
