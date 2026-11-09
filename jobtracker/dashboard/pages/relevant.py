"""'Relevant For Me' page (default): strong + possible matches.

Thin view: collects filter input, calls ``dashboard_service.list_relevant_jobs``,
and renders the resulting job cards with new-job badges, fit score, concerns,
and apply + source links. No business logic lives here.
"""

from __future__ import annotations

import streamlit as st

from jobtracker.dashboard._shared import render_job_card, session_scope
from jobtracker.services import dashboard_service
from jobtracker.services.dashboard_service import JobFilters


def render() -> None:
    st.title("Relevant For Me")
    st.caption(
        "Strong and possible mechanical-engineering matches for your profile. "
        "Adjacent roles appear as possible matches and are never hidden."
    )

    cols = st.columns([4, 1])
    cols[0].caption(
        "New postings appear here after each scheduler poll (≤1h per source). "
        "Use Refresh to re-query after a poll."
    )
    if cols[1].button("Refresh", key="refresh-relevant"):
        st.rerun()

    with session_scope() as session:
        options = dashboard_service.filter_options(session)

        with st.sidebar:
            st.header("Filters")
            relevant_options = [
                c
                for c in options.classifications
                if c in dashboard_service.RELEVANT_CLASSIFICATIONS
            ]
            classifications = st.multiselect("Classification", options=relevant_options)
            term_seasons = st.multiselect("Term season", options=options.term_seasons)
            countries = st.multiselect("Country", options=options.countries)
            locations_query = st.text_input("Location contains")
            remote_statuses = st.multiselect("Remote status", options=options.remote_statuses)
            employment_types = st.multiselect("Employment type", options=options.employment_types)
            eligibility_statuses = st.multiselect(
                "Sponsorship eligibility", options=options.eligibility_statuses
            )
            fit_range = st.slider("Fit score range", 0, 100, (0, 100))
            freshness_days = st.number_input(
                "Freshness window (days, 0 = any)", min_value=0, max_value=365, value=0
            )
            only_new = st.checkbox("Only new postings")

        filters = JobFilters(
            classifications=classifications or None,
            term_seasons=term_seasons or None,
            countries=countries or None,
            locations_query=locations_query or None,
            remote_statuses=remote_statuses or None,
            employment_types=employment_types or None,
            eligibility_statuses=eligibility_statuses or None,
            min_fit_score=fit_range[0],
            max_fit_score=fit_range[1],
            freshness_days=int(freshness_days) or None,
            only_new=only_new,
        )
        jobs = dashboard_service.list_relevant_jobs(session, filters)

        st.write(f"**{len(jobs)}** matching postings.")
        for view in jobs:
            render_job_card(view)
