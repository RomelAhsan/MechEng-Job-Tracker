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


def seed_profile_data() -> dict:
    """Return the config/user.yaml profile as a dict to seed a new user."""
    try:
        return load_profile().model_dump()
    except Exception:  # noqa: BLE001 - seeding is best-effort, never blocks login.
        return {}


def current_user_id() -> int | None:
    """Return the authenticated user id from Streamlit session state, if any."""
    import streamlit as st

    return st.session_state.get("user_id")


def current_user_role() -> str:
    """Return the authenticated user's role ('admin' or 'member')."""
    import streamlit as st

    return st.session_state.get("user_role") or "member"


def is_admin() -> bool:
    """Return True if the current user is an admin (owns the company registry)."""
    return current_user_role() == "admin"


def require_login() -> int:
    """Stop rendering the current page unless a user is logged in.

    Pages call this first; it returns the authenticated user id or halts the
    page with a message (the login gate lives in ``app.py``).
    """
    import streamlit as st

    user_id = current_user_id()
    if user_id is None:
        st.warning("Please sign in to use the dashboard.")
        st.stop()
    return user_id


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


def _run_ai_analysis(job_id: str, *, user_id: int | None = None) -> None:
    """Thin wiring for the job-card AI button: run analysis and show the result.

    Delegates all logic to the AI service. Never raises on a missing key: the
    service returns an informative 'add a Gemini API key' result which is shown
    as a warning with a link to the AI Analysis page.
    """
    import streamlit as st

    from jobtracker.services import ai_service

    with write_session_scope() as session:
        result = ai_service.analyze_job(session, job_id, user_id=user_id)

    if not result.available:
        st.warning(result.message or "AI unavailable - add a Gemini API key.")
        st.caption("Open the **AI Analysis** page to add a key and manage analyses.")
        return

    data = result.result
    st.success(
        f"AI: score {data.get('relevance_score')} · "
        f"{data.get('recommendation')} · confidence {result.confidence}"
    )
    if data.get("explanation"):
        st.caption(data["explanation"])


def render_job_card(view: JobView, *, user_id: int | None = None) -> None:
    """Render a single job as a thin presentation card.

    Shows the title, badges, fit score, concerns, and the direct application +
    source links. When ``user_id`` is given the Save (heart) button is
    functional and the current application status is shown. No filtering or
    scoring happens here; the Save action delegates to the service layer.
    """
    import streamlit as st

    saved_badge = " ❤️" if view.is_saved else ""
    badge = " 🆕 **NEW**" if view.is_new else ""
    header = f"{view.title} — {view.company_name}{badge}{saved_badge}"
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
        # Stage-1 deterministic eligibility verdict. Jobs that fail the hard
        # filter are removed from every list, so a surfaced card is normally
        # eligible; show why it passed (and any caveats/evidence it carries).
        if view.eligibility_reasons:
            st.caption(
                "Eligibility: " + "; ".join(view.eligibility_reasons)
            )
        if view.eligible is False and view.eligibility_evidence:
            st.caption("Eligibility evidence: " + "; ".join(view.eligibility_evidence))

        link_cols = st.columns(2)
        if view.application_url:
            link_cols[0].markdown(f"[Apply]({view.application_url})")
        else:
            link_cols[0].write("Apply: —")
        if view.source_url:
            link_cols[1].markdown(f"[Source posting]({view.source_url})")
        else:
            link_cols[1].write("Source: —")

        if view.application_status:
            st.info(f"Pipeline status: **{view.application_status}**")

        action_cols = st.columns(2)
        if user_id is not None:
            label = "♥ Saved" if view.is_saved else "♡ Save"
            if action_cols[0].button(label, key=f"save-{view.job_id}"):
                from jobtracker.services import saved_job_service

                with write_session_scope() as session:
                    saved_job_service.toggle_saved(session, user_id, view.job_id)
                st.rerun()
        else:
            action_cols[0].button(
                "Save", key=f"save-{view.job_id}", disabled=True,
                help="Sign in to save jobs.",
            )
        if action_cols[1].button(
            "AI analysis", key=f"ai-{view.job_id}",
            help="Run the built-in AI relevance engine on this job.",
        ):
            _run_ai_analysis(view.job_id, user_id=user_id)

        if view.skills:
            st.caption("Skills: " + ", ".join(view.skills))
        if view.description:
            with st.expander("Full description", expanded=False):
                st.write(view.description)
