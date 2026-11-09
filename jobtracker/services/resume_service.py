"""Resume Studio service (SPEC Phase 4).

Ties the pure resume modules (:mod:`jobtracker.resume.parser`,
:mod:`jobtracker.resume.generator`, :mod:`jobtracker.resume.export`) to the DB
and user/saved-job layers so the CLI and Streamlit pages stay thin:

* :func:`upload_master` stores the UNTOUCHED original upload (base64) plus the
  parsed SPEC sections,
* :func:`list_masters` / :func:`get_master` read them back,
* :func:`generate_for_job` tailors the latest master resume to one of the
  user's saved jobs, runs the deterministic fact-preservation verifier, renders
  Markdown + JSON + ATS PDF, and persists the :class:`GeneratedResume`,
* :func:`get_generated` / :func:`list_generated` read drafts back.

An AI provider (FEAT-006) MAY optionally suggest emphasis, but the verifier is
authoritative and always runs, so AI can never introduce fabrications. With no
key (NullProvider) generation still works via deterministic reorder/emphasis.

Nothing here imports Streamlit.
"""

from __future__ import annotations

import base64
import logging
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from jobtracker.db.models import GeneratedResume, Job, MasterResume
from jobtracker.resume import export as resume_export
from jobtracker.resume.generator import TailoredResume, generate_tailored_resume
from jobtracker.resume.parser import ParsedResume, parse_master_resume
from jobtracker.services import saved_job_service

logger = logging.getLogger("jobtracker.services.resume")


class ResumeServiceError(ValueError):
    """Raised on invalid resume operations (unknown master/job, not saved)."""


def _now() -> datetime:
    return datetime.now(UTC)


# --- Master resume upload / read ------------------------------------------


def upload_master(
    session: Session,
    user_id: int,
    filename: str,
    data: bytes,
    *,
    content_type: str | None = None,
) -> MasterResume:
    """Parse and store a user's master resume, preserving the original bytes.

    The original upload is stored base64-encoded and never modified. The parsed
    SPEC sections and raw text are stored alongside for tailoring.
    """
    if not data:
        raise ResumeServiceError("Uploaded resume is empty.")
    parsed = parse_master_resume(filename, data)
    row = MasterResume(
        user_id=user_id,
        original_filename=filename,
        content_type=content_type,
        original_base64=base64.b64encode(data).decode("ascii"),
        raw_text=parsed.raw_text,
        parsed_sections=parsed.to_dict(),
        uploaded_at=_now(),
    )
    session.add(row)
    session.flush()
    logger.info(
        "Stored master resume %s for user %s (%d bytes, %d sections)",
        row.id,
        user_id,
        len(data),
        len(parsed.sections),
    )
    return row


def list_masters(session: Session, user_id: int) -> list[MasterResume]:
    """Return a user's master resumes, newest first."""
    return list(
        session.scalars(
            select(MasterResume)
            .where(MasterResume.user_id == user_id)
            .order_by(MasterResume.id.desc())
        ).all()
    )


def get_master(session: Session, master_id: int) -> MasterResume | None:
    """Return a master resume by id."""
    return session.get(MasterResume, master_id)


def latest_master(session: Session, user_id: int) -> MasterResume | None:
    """Return the user's most recently uploaded master resume, if any."""
    masters = list_masters(session, user_id)
    return masters[0] if masters else None


def original_bytes(master: MasterResume) -> bytes:
    """Return the preserved original upload bytes (decoded from base64)."""
    if not master.original_base64:
        return b""
    return base64.b64decode(master.original_base64)


def parsed_resume(master: MasterResume) -> ParsedResume:
    """Reconstruct the :class:`ParsedResume` from a stored master row."""
    if master.parsed_sections:
        return ParsedResume.from_dict(master.parsed_sections)
    return ParsedResume(raw_text=master.raw_text or "")


# --- Tailored generation ---------------------------------------------------


def _job_dict(job: Job) -> dict:
    return {
        "title": job.title or "",
        "description": job.description or "",
        "requirements": list(job.requirements or []),
        "preferred_qualifications": list(job.preferred_qualifications or []),
        "skills": list(job.skills or []),
    }


@dataclass
class GenerationOutcome:
    """Presentation-ready result of generating a tailored resume.

    ``row`` is the persisted draft; ``tailored`` is the in-memory tailoring
    result (resume + change log + keyword reports). ``ai_assisted`` records
    whether an AI provider contributed emphasis hints (the verifier still ran).
    """

    row: GeneratedResume
    tailored: TailoredResume
    ai_assisted: bool = False


def generate_for_job(
    session: Session,
    user_id: int,
    job_id: str,
    *,
    master_id: int | None = None,
) -> GenerationOutcome:
    """Generate (and persist) a tailored resume for a user's saved job.

    Uses the given master resume (or the user's latest) and the saved job,
    reorders/emphasizes grounded content, runs ``verify_claims``, renders all
    three output formats, and upserts the :class:`GeneratedResume`. Raises
    :class:`ResumeServiceError` if the master or job is missing, or the job is
    not in the user's saved list.
    """
    master = (
        get_master(session, master_id)
        if master_id is not None
        else latest_master(session, user_id)
    )
    if master is None or master.user_id != user_id:
        raise ResumeServiceError(
            "No master resume found. Upload a master resume first."
        )

    job = session.get(Job, job_id)
    if job is None:
        raise ResumeServiceError(f"Unknown job_id '{job_id}'.")
    if not saved_job_service.is_saved(session, user_id, job_id):
        raise ResumeServiceError(
            "Tailored resumes can only be generated for saved jobs. "
            "Save this job first."
        )

    master_resume = parsed_resume(master)
    tailored = generate_tailored_resume(master_resume, _job_dict(job))

    heading = _resume_heading(master_resume, job)
    markdown = resume_export.to_markdown(tailored.resume, heading=heading)
    json_doc = resume_export.to_json(tailored.resume)
    pdf_bytes = resume_export.to_pdf(tailored.resume, heading=heading)

    now = _now()
    row = _find_generated(session, user_id, job_id, master.id)
    if row is None:
        row = GeneratedResume(
            user_id=user_id,
            job_id=job_id,
            master_resume_id=master.id,
            created_at=now,
        )
        session.add(row)
    row.markdown = markdown
    row.json_sections = tailored.resume.to_dict()
    row.pdf_base64 = base64.b64encode(pdf_bytes).decode("ascii")
    row.change_log = list(tailored.change_log)
    row.missing_keywords = list(tailored.missing_keywords)
    row.matched_keywords = list(tailored.matched_keywords)
    row.updated_at = now
    session.flush()

    # json_doc is the canonical serialized structure; keep it available to
    # callers via the tailored object without a second DB column.
    logger.info(
        "Generated tailored resume %s for user %s job %s (%d missing kw)",
        row.id,
        user_id,
        job_id,
        len(tailored.missing_keywords),
    )
    _ = json_doc  # rendered for API parity; markdown/json_sections persisted
    return GenerationOutcome(row=row, tailored=tailored, ai_assisted=False)


def _resume_heading(resume: ParsedResume, job: Job) -> str:
    """Pick a heading for the tailored output from grounded contact data."""
    contact = resume.sections.get("contact") or []
    for line in contact:
        if "@" not in line and not any(ch.isdigit() for ch in line):
            return line
    summary = resume.sections.get("summary") or []
    if summary:
        first = summary[0]
        if len(first) <= 60:
            return first
    return "Tailored Resume"


def _find_generated(
    session: Session, user_id: int, job_id: str, master_id: int | None
) -> GeneratedResume | None:
    return session.scalars(
        select(GeneratedResume).where(
            GeneratedResume.user_id == user_id,
            GeneratedResume.job_id == job_id,
            GeneratedResume.master_resume_id == master_id,
        )
    ).first()


def get_generated(session: Session, generated_id: int) -> GeneratedResume | None:
    """Return a generated resume draft by id."""
    return session.get(GeneratedResume, generated_id)


def get_generated_for_job(
    session: Session, user_id: int, job_id: str
) -> GeneratedResume | None:
    """Return the latest generated draft for a user's job, if any."""
    return session.scalars(
        select(GeneratedResume)
        .where(
            GeneratedResume.user_id == user_id,
            GeneratedResume.job_id == job_id,
        )
        .order_by(GeneratedResume.id.desc())
    ).first()


def list_generated(session: Session, user_id: int) -> list[GeneratedResume]:
    """Return a user's generated resume drafts, newest first."""
    return list(
        session.scalars(
            select(GeneratedResume)
            .where(GeneratedResume.user_id == user_id)
            .order_by(GeneratedResume.id.desc())
        ).all()
    )


def generated_pdf_bytes(row: GeneratedResume) -> bytes:
    """Return the stored ATS PDF bytes for a generated draft."""
    if not row.pdf_base64:
        return b""
    return base64.b64decode(row.pdf_base64)
