"""'My Profile' page (Phase 2).

Thin view letting the signed-in user edit their personalization profile. Saving
delegates to ``user_service.update_profile`` which bumps ``profile_version`` on
change (so Phase 3 AI caches invalidate). No business logic lives here.
"""

from __future__ import annotations

import streamlit as st

from jobtracker.dashboard._shared import require_login, write_session_scope
from jobtracker.services import user_service


def _split(value: str) -> list[str]:
    return [part.strip() for part in value.split(",") if part.strip()]


def render() -> None:
    user_id = require_login()
    st.title("My Profile")
    st.caption(
        "Your profile drives relevance scoring. Editing it bumps the profile "
        "version so any cached AI analysis is refreshed."
    )

    with write_session_scope() as session:
        profile = user_service.get_profile(session, user_id)
        data = dict(profile.data or {}) if profile else {}

        st.caption(f"Profile version: {profile.profile_version if profile else 1}")

        with st.form("profile-form"):
            name = st.text_input("Name", value=data.get("name", ""))
            email = st.text_input("Email", value=data.get("email", ""))
            university = st.text_input(
                "University", value=data.get("university", "University of Waterloo")
            )
            program = st.text_input(
                "Program", value=data.get("program", "Mechanical Engineering")
            )
            year = st.number_input(
                "Year", min_value=1, max_value=10, value=int(data.get("year", 1) or 1)
            )
            citizenship = st.text_input(
                "Citizenship (comma-separated)",
                value=", ".join(data.get("citizenship", []) or []),
            )
            work_authorization = st.text_input(
                "Work authorization (comma-separated)",
                value=", ".join(data.get("work_authorization", []) or []),
            )
            preferred_locations = st.text_input(
                "Preferred locations (comma-separated)",
                value=", ".join(data.get("preferred_locations", []) or []),
            )
            available_terms = st.text_input(
                "Available terms (comma-separated)",
                value=", ".join(data.get("available_terms", []) or []),
            )
            interests = st.text_input(
                "Interests (comma-separated)",
                value=", ".join(data.get("interests", []) or []),
            )
            skills = st.text_input(
                "Skills (comma-separated)",
                value=", ".join(data.get("skills", []) or []),
            )
            submitted = st.form_submit_button("Save profile")

        if submitted:
            new_data = dict(data)
            new_data.update(
                {
                    "name": name,
                    "email": email,
                    "university": university,
                    "program": program,
                    "year": int(year),
                    "citizenship": _split(citizenship),
                    "work_authorization": _split(work_authorization),
                    "preferred_locations": _split(preferred_locations),
                    "available_terms": _split(available_terms),
                    "interests": _split(interests),
                    "skills": _split(skills),
                }
            )
            updated = user_service.update_profile(session, user_id, new_data)
            st.success(f"Profile saved (version {updated.profile_version}).")
            st.rerun()
