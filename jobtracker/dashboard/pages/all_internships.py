"""'All Internships' page: the full active DB with advanced filters.

Thin view over ``dashboard_service.list_jobs``. Includes a "Show removed
(archive)" toggle and source info plus direct application/source links. All
filtering/querying happens in the service.
"""

from __future__ import annotations

import streamlit as st

from jobtracker.dashboard._shared import render_job_card, session_scope
from jobtracker.services import dashboard_service
from jobtracker.services.dashboard_service import JobFilters


def render() -> None:
    st.title("All Internships")
    st.caption(
        "Every tracked posting, including ambiguous and lower-fit roles. "
        "Hard-filter caveats are surfaced as concerns, never hidden."
    )

    with session_scope() as session:
        options = dashboard_service.filter_options(session)

        with st.sidebar:
            st.header("Filters")
            include_removed = st.toggle("Show removed (archive)", value=False)
            classifications = st.multiselect("Classification", options=options.classifications)
            term_seasons = st.multiselect("Term season", options=options.term_seasons)
            countries = st.multiselect("Country", options=options.countries)
            locations_query = st.text_input("Location contains")
            remote_statuses = st.multiselect("Remote status", options=options.remote_statuses)
            employment_types = st.multiselect("Employment type", options=options.employment_types)
            eligibility_statuses = st.multiselect(
                "Sponsorship eligibility", options=options.eligibility_statuses
            )
            sponsorship_statuses = st.multiselect(
                "Sponsorship status", options=options.sponsorship_statuses
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
            sponsorship_statuses=sponsorship_statuses or None,
            min_fit_score=fit_range[0],
            max_fit_score=fit_range[1],
            freshness_days=int(freshness_days) or None,
            only_new=only_new,
            include_removed=include_removed,
        )
        jobs = dashboard_service.list_jobs(session, filters)

        st.write(f"**{len(jobs)}** postings{' (incl. removed)' if include_removed else ''}.")
        for view in jobs:
            render_job_card(view)
