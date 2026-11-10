"""'AI Analysis' page (Phase 3).

Thin view over :mod:`jobtracker.services.ai_service` and the
export-for-external-AI helper in :mod:`jobtracker.services.export_service`:
pick jobs, run the built-in AI analysis, view cached results, apply a manual
override, and one-click export for pasting into an external AI.

When no Gemini API key is configured the page shows an informative panel that
links Google AI Studio instead of erroring. No business logic lives here.
"""

from __future__ import annotations

import json

import streamlit as st

from jobtracker.ai.provider import NO_KEY_MESSAGE, get_provider
from jobtracker.dashboard._shared import (
    require_login,
    session_scope,
    write_session_scope,
)
from jobtracker.services import ai_service, dashboard_service, export_service
from jobtracker.settings import get_settings


def _render_no_key_panel() -> None:
    st.warning("AI analysis is not configured yet.")
    st.markdown(
        "To enable the built-in AI relevance engine, create a free Gemini API "
        "key at [Google AI Studio](https://aistudio.google.com/apikey), "
        "then set `GEMINI_API_KEY=<your key>` in your `.env` and restart the "
        "dashboard."
    )
    st.caption(NO_KEY_MESSAGE)
    st.info(
        "You can still use **Export for external AI** below to download a clean "
        "job + profile summary and paste it into any AI chat."
    )


def _render_result(result: ai_service.AnalysisResult) -> None:
    if not result.available:
        st.warning(result.message or "AI unavailable - add a Gemini API key.")
        return
    data = result.result
    cols = st.columns(3)
    cols[0].metric("Relevance", data.get("relevance_score", "—"))
    cols[1].metric("Recommendation", str(data.get("recommendation", "—")))
    cols[2].metric("Confidence", result.confidence if result.confidence is not None else "—")
    badge = " (manual override)" if result.overridden else ""
    source = "cache" if result.cache_hit else f"{result.provider}:{result.model}"
    st.caption(f"Source: {source}{badge}")
    if data.get("explanation"):
        st.write(data["explanation"])
    with st.expander("Full analysis JSON", expanded=False):
        st.json(data)


def render() -> None:
    user_id = require_login()
    st.title("AI Analysis")
    st.caption(
        "Run the built-in AI relevance engine on specific jobs, review cached "
        "results, override a classification, or export jobs for an external AI."
    )

    settings = get_settings()
    provider = get_provider(settings)
    if not provider.available:
        _render_no_key_panel()

    with session_scope() as session:
        views = dashboard_service.list_jobs(session, user_id=user_id)

    if not views:
        st.info("No jobs available yet. Run an ingestion pass first.")
        return

    labels = {f"{v.title} — {v.company_name} ({v.job_id})": v.job_id for v in views}

    st.subheader("Analyze a job")
    choice = st.selectbox("Job", options=list(labels.keys()), key="ai-job-select")
    selected_job_id = labels.get(choice)
    force = st.checkbox("Force re-run (ignore cache)", key="ai-force")

    run_cols = st.columns(2)
    if run_cols[0].button("Run AI analysis", key="ai-run") and selected_job_id:
        with write_session_scope() as session:
            result = ai_service.analyze_job(session, selected_job_id, force=force)
        _render_result(result)
    elif selected_job_id:
        with session_scope() as session:
            cached = ai_service.get_cached(session, selected_job_id, user_id=user_id)
        if cached is not None:
            st.caption("Showing cached analysis:")
            _render_result(cached)

    with st.expander("Manual override", expanded=False):
        st.caption(
            "Paste a strict-JSON analysis (same SPEC fields) to override the "
            "stored classification for this job."
        )
        override_text = st.text_area("Override JSON", key="ai-override-json", height=160)
        if st.button("Save override", key="ai-override-save") and selected_job_id:
            try:
                payload = json.loads(override_text)
                with write_session_scope() as session:
                    result = ai_service.override_analysis(
                        session, selected_job_id, payload, user_id=user_id
                    )
                st.success("Override saved.")
                _render_result(result)
            except (json.JSONDecodeError, ai_service.AiServiceError) as exc:
                st.error(f"Invalid override: {exc}")

    st.divider()
    st.subheader("Export for external AI")
    st.caption(
        "Download a clean export (jobs + your profile summary) to paste into "
        "any external AI chat."
    )
    scope = st.radio(
        "What to export",
        options=["This job", "My saved jobs", "All active jobs"],
        horizontal=True,
        key="ai-export-scope",
    )
    fmt = st.selectbox("Format", options=["markdown", "json", "csv"], key="ai-export-fmt")
    if st.button("Build export", key="ai-export-build"):
        with session_scope() as session:
            if scope == "This job" and selected_job_id:
                body = export_service.render_ai_export(
                    session, fmt, job_ids=[selected_job_id], user_id=user_id
                )
            elif scope == "My saved jobs":
                body = export_service.render_ai_export(
                    session, fmt, saved_only=True, user_id=user_id
                )
            else:
                body = export_service.render_ai_export(session, fmt, user_id=user_id)
        ext = {"markdown": "md", "json": "json", "csv": "csv"}[fmt]
        st.download_button(
            "Download export",
            data=body,
            file_name=f"ai-export.{ext}",
            key="ai-export-download",
        )
        with st.expander("Preview", expanded=False):
            st.code(body[:5000])
