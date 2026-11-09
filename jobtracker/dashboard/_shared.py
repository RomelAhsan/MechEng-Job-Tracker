"""Shared helpers for the Streamlit dashboard pages.

These helpers only wire the view layer to the service/session layer; they hold
no business logic. Pages import them to obtain a DB session and the active user
profile, and to render common widgets (job cards, links).
"""

from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime
from typing import TYPE_CHECKING

from jobtracker.db.base import create_session_factory

if TYPE_CHECKING:  # pragma: no cover - typing only
    from collections.abc import Iterator

    from sqlalchemy.orm import Session

    from jobtracker.schemas.user_profile import UserProfile
    from jobtracker.services.dashboard_service import JobView


@contextmanager
def session_scope() -> Iterator[Session]:
    """Yield a short-lived read session for a page render."""
    factory = create_session_factory()
    session = factory()
    try:
        yield session
    finally:
        session.close()


@contextmanager
def write_session_scope() -> Iterator[Session]:
    """Yield a session that commits on success and rolls back on error."""
    factory = create_session_factory()
    session = factory()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def load_profile() -> UserProfile:
    """Load the single implicit Phase-1 user profile from config/user.yaml."""
    from jobtracker.config.user_loader import load_user_profile
    from jobtracker.settings import get_settings

    return load_user_profile(get_settings().user_file)


def _fmt_dt(value: datetime | None) -> str:
    return value.strftime("%Y-%m-%d") if value is not None else "—"


def _fmt_dt_time(value: datetime | None) -> str:
    return value.strftime("%Y-%m-%d %H:%M") if value is not None else "—"


def _fmt_rel(value: datetime | None, ago: str | None) -> str:
    """Combine a relative label with an absolute timestamp for transparency."""
    if value is None:
        return "—"
    absolute = _fmt_dt_time(value)
    return f"{ago} ({absolute})" if ago else absolute


def render_job_card(view: JobView) -> None:
    """Render a single job as a thin presentation card.

    Shows the title, badges, fit score, concerns, and the direct application +
    source links. No filtering or scoring happens here.
    """
    import streamlit as st

    badge = " 🆕 **NEW**" if view.is_new else ""
    header = f"{view.title} — {view.company_name}{badge}"
    with st.expander(header, expanded=False):
        cols = st.columns(3)
        cols[0].metric("Fit score", view.fit_score if view.fit_score is not None else "—")
        cols[1].write(f"**Classification:** {view.classification or 'unscreened'}")
        cols[2].write(f"**Status:** {view.status}")

        st.write(
            f"**Location:** {view.location or '—'}  |  "
            f"**Remote:** {view.remote_status}  |  "
            f"**Type:** {view.employment_type}"
        )
        duration = view.duration_label or (
            f"{view.duration_months} months" if view.duration_months else "—"
        )
        st.write(
            f"**Term:** {view.term_season}  |  **Duration:** {duration}  |  "
            f"**Posted:** {_fmt_dt(view.posted_at)}"
        )
        st.write(
            f"**Eligibility:** {view.eligibility_status or 'unknown'}  |  "
            f"**Sponsorship:** {view.visa_sponsorship}"
        )
        # Freshness / timing transparency: boards do not push to us, so we show
        # exactly when the posting was first seen and when its source was last
        # polled (relative + absolute).
        st.caption(
            f"First seen: {_fmt_rel(view.first_seen_at, view.first_seen_ago)}  |  "
            f"Source last checked: "
            f"{_fmt_rel(view.source_last_checked_at, view.source_last_checked_ago)}"
        )

        if view.concerns:
            st.warning("Concerns: " + "; ".join(view.concerns))
        if view.classification_reasons:
            st.caption("Reasons: " + "; ".join(view.classification_reasons))

        link_cols = st.columns(2)
        if view.application_url:
            link_cols[0].markdown(f"[Apply]({view.application_url})")
        else:
            link_cols[0].write("Apply: —")
        if view.source_url:
            link_cols[1].markdown(f"[Source posting]({view.source_url})")
        else:
            link_cols[1].write("Source: —")

        # Phase 1: Save / AI buttons are rendered but intentionally disabled.
        action_cols = st.columns(2)
        action_cols[0].button(
            "Save", key=f"save-{view.job_id}", disabled=True,
            help="Saving jobs is available in a later phase.",
        )
        action_cols[1].button(
            "AI analysis", key=f"ai-{view.job_id}", disabled=True,
            help="AI analysis is available in a later phase.",
        )

        if view.skills:
            st.caption("Skills: " + ", ".join(view.skills))
        if view.description:
            with st.expander("Full description", expanded=False):
                st.write(view.description)
