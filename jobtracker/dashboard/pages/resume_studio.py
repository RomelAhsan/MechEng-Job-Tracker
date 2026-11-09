"""'Resume Studio' page (Phase 4).

Thin view over :mod:`jobtracker.services.resume_service`: upload a master
resume, view the extracted SPEC sections, generate a tailored resume for a
saved job, download the PDF / Markdown / JSON, and review the change log and
missing-keyword report. All business logic (parsing, tailoring, the
authoritative fact-preservation verifier, and export) lives in the service and
pure-function layers; no logic lives here.
"""

from __future__ import annotations

import streamlit as st

from jobtracker.dashboard._shared import (
    require_login,
    session_scope,
    write_session_scope,
)
from jobtracker.services import dashboard_service, resume_service


def _render_sections(parsed: dict) -> None:
    sections = (parsed or {}).get("sections") or {}
    if not sections:
        st.info("No sections were extracted from this resume.")
        return
    for name, body in sections.items():
        with st.expander(f"{name.title()} ({len(body)})", expanded=False):
            for line in body:
                st.write(f"- {line}")


def _render_downloads(row) -> None:  # noqa: ANN001 - GeneratedResume row
    cols = st.columns(3)
    cols[0].download_button(
        "Download PDF",
        data=resume_service.generated_pdf_bytes(row),
        file_name=f"resume-{row.job_id}.pdf",
        mime="application/pdf",
        key=f"dl-pdf-{row.id}",
    )
    cols[1].download_button(
        "Download Markdown",
        data=row.markdown or "",
        file_name=f"resume-{row.job_id}.md",
        mime="text/markdown",
        key=f"dl-md-{row.id}",
    )
    import json

    cols[2].download_button(
        "Download JSON",
        data=json.dumps(row.json_sections or {}, indent=2),
        file_name=f"resume-{row.job_id}.json",
        mime="application/json",
        key=f"dl-json-{row.id}",
    )


def _render_report(row) -> None:  # noqa: ANN001 - GeneratedResume row
    if row.change_log:
        st.markdown("**Change log** (what was reordered / emphasized):")
        for entry in row.change_log:
            st.write(f"- {entry}")
    if row.missing_keywords:
        st.markdown("**Missing-keyword report** (suggestions only - never added):")
        st.caption(
            "These job keywords were not found in your master resume, so they "
            "were NOT inserted. Add real evidence to your master resume if they "
            "apply to you."
        )
        st.write(", ".join(row.missing_keywords))
    if row.matched_keywords:
        st.caption("Matched keywords you already evidence: " + ", ".join(row.matched_keywords))


def render() -> None:
    user_id = require_login()
    st.title("Resume Studio")
    st.caption(
        "Upload your master resume, then generate a tailored resume for any "
        "saved job. Tailoring only reorders and emphasizes facts already in "
        "your master resume - it never invents experience, skills, or dates."
    )

    # --- Upload a master resume -------------------------------------------
    st.subheader("1. Master resume")
    with session_scope() as session:
        masters = resume_service.list_masters(session, user_id)

    upload = st.file_uploader(
        "Upload master resume (PDF, DOCX, Markdown, or plain text)",
        type=["pdf", "docx", "md", "markdown", "txt"],
        key="resume-upload",
    )
    if upload is not None and st.button("Save master resume", key="resume-save"):
        data = upload.getvalue()
        try:
            with write_session_scope() as session:
                resume_service.upload_master(
                    session, user_id, upload.name, data, content_type=upload.type
                )
            st.success(f"Stored and parsed '{upload.name}'. Original preserved.")
            st.rerun()
        except resume_service.ResumeServiceError as exc:
            st.error(str(exc))

    if not masters:
        st.info("Upload a master resume to begin.")
        return

    latest = masters[0]
    st.write(
        f"Using latest master: **{latest.original_filename}** "
        f"(uploaded {latest.uploaded_at:%Y-%m-%d %H:%M} UTC)"
        if latest.uploaded_at
        else f"Using latest master: **{latest.original_filename}**"
    )
    st.download_button(
        "Download original (preserved untouched)",
        data=resume_service.original_bytes(latest),
        file_name=latest.original_filename or "master-resume",
        key="resume-dl-original",
    )
    with st.expander("Extracted sections", expanded=False):
        _render_sections(latest.parsed_sections or {})

    # --- Generate a tailored resume for a saved job -----------------------
    st.divider()
    st.subheader("2. Generate for a saved job")
    with session_scope() as session:
        saved_views = dashboard_service.list_saved_job_views(session, user_id)

    if not saved_views:
        st.info(
            "No saved jobs yet. Heart a job from any listing (or the "
            "Saved / Pipeline page) to tailor a resume for it."
        )
        return

    labels = {f"{v.title} — {v.company_name} ({v.job_id})": v.job_id for v in saved_views}
    choice = st.selectbox("Saved job", options=list(labels.keys()), key="resume-job")
    job_id = labels.get(choice)

    if st.button("Generate tailored resume", key="resume-generate") and job_id:
        try:
            with write_session_scope() as session:
                outcome = resume_service.generate_for_job(session, user_id, job_id)
                st.session_state["resume_last_generated_job"] = job_id
            st.success(
                f"Generated a tailored resume ({len(outcome.tailored.change_log)} "
                f"change-log entries)."
            )
            st.rerun()
        except resume_service.ResumeServiceError as exc:
            st.error(str(exc))

    # --- Show the latest draft for the selected job -----------------------
    if job_id:
        with session_scope() as session:
            draft = resume_service.get_generated_for_job(session, user_id, job_id)
        if draft is not None:
            st.markdown("### Tailored resume")
            _render_downloads(draft)
            _render_report(draft)
            with st.expander("Preview (Markdown)", expanded=False):
                st.code(draft.markdown or "", language="markdown")
