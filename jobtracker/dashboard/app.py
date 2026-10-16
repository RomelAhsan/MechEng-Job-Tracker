"""Streamlit dashboard entry point (design 11).

Builds the Phase-1 navigation (``st.navigation``) over the four pages. Each page
is a thin view that calls the service layer only; this module wires them
together and holds no business logic. Launch via ``python -m jobtracker
dashboard`` which shells out to ``streamlit run jobtracker/dashboard/app.py``.
"""

from __future__ import annotations

import streamlit as st

from jobtracker.dashboard.pages import (
    all_internships,
    companies,
    relevant,
    source_health,
)


def main() -> None:
    st.set_page_config(page_title="MechEng Co-op Job Tracker", layout="wide")

    pages = [
        st.Page(relevant.render, title="Relevant For Me", default=True),
        st.Page(all_internships.render, title="All Internships"),
        st.Page(companies.render, title="Companies"),
        st.Page(source_health.render, title="Source Health"),
    ]
    st.navigation(pages).run()


# Streamlit executes the script top to bottom; call main on import/run.
main()
