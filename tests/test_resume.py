"""Tests for the Phase-4 Resume Studio (FEAT-007).

Covers:

* section parsing from Markdown/text fixtures, plus in-test generated PDF and
  DOCX fixtures (so PDF/DOCX extraction is exercised without committing binary
  files),
* strict fact preservation: a job keyword NOT in the master is reported as
  missing and never inserted; an ungrounded claim is stripped/flagged by
  ``verify_claims``,
* the change log is produced,
* Markdown + JSON + ATS PDF all render without error,
* the service persists a grounded draft with the original preserved untouched.
"""

from __future__ import annotations

import io
import json
from pathlib import Path

from sqlalchemy.orm import Session

from jobtracker.db.models import Company, Job
from jobtracker.resume import export as resume_export
from jobtracker.resume.generator import (
    generate_tailored_resume,
    is_grounded,
    verify_claims,
)
from jobtracker.resume.parser import ParsedResume, parse_master_resume
from jobtracker.services import auth_service, resume_service, saved_job_service

FIXTURES = Path(__file__).parent / "fixtures"


def _master_markdown_bytes() -> bytes:
    return (FIXTURES / "master_resume.md").read_bytes()


def _job() -> dict:
    return {
        "title": "Mechanical Engineering Intern",
        "description": (
            "Seeking a student with SolidWorks and CATIA experience. "
            "Kubernetes and GraphQL are a plus. FEA in ANSYS required."
        ),
        "requirements": ["SolidWorks", "FEA", "Python"],
        "preferred_qualifications": ["CATIA", "Kubernetes"],
        "skills": ["SolidWorks", "CATIA", "GraphQL"],
    }


# --- Parsing ---------------------------------------------------------------


def test_parse_markdown_sections() -> None:
    parsed = parse_master_resume("master_resume.md", _master_markdown_bytes())
    sections = parsed.sections
    for expected in (
        "contact",
        "summary",
        "education",
        "experience",
        "projects",
        "skills",
        "certifications",
        "leadership",
        "awards",
    ):
        assert expected in sections, f"missing section {expected}"
    # Contact harvested the email and phone.
    assert any("@" in line for line in sections["contact"])
    # Links harvested from the document.
    assert any("github.com/janedoe" in link for link in parsed.links)
    # Bullets are cleaned of the leading dash.
    assert any("SolidWorks" in line for line in sections["skills"])


def test_parse_plain_text() -> None:
    text = (
        "John Smith\njohn@example.com\n\n"
        "EXPERIENCE\nBuilt pumps at Acme\n\n"
        "SKILLS\nPython, CAD\n"
    )
    parsed = parse_master_resume("resume.txt", text.encode("utf-8"))
    assert "experience" in parsed.sections
    assert "skills" in parsed.sections
    assert any("Acme" in line for line in parsed.sections["experience"])


def test_parse_docx_roundtrip() -> None:
    import docx

    document = docx.Document()
    document.add_paragraph("Jane Doe")
    document.add_paragraph("Experience")
    document.add_paragraph("Designed brackets in SolidWorks")
    document.add_paragraph("Skills")
    document.add_paragraph("SolidWorks, Python")
    buffer = io.BytesIO()
    document.save(buffer)

    parsed = parse_master_resume("resume.docx", buffer.getvalue())
    assert "experience" in parsed.sections
    assert "skills" in parsed.sections
    assert any("SolidWorks" in line for line in parsed.sections["experience"])


def test_parse_pdf_roundtrip() -> None:
    # Build a tiny PDF in-test via reportlab so we exercise pypdf extraction
    # without committing a binary fixture.
    from reportlab.pdfgen import canvas

    buffer = io.BytesIO()
    pdf = canvas.Canvas(buffer)
    text = pdf.beginText(72, 720)
    for line in ("Jane Doe", "Experience", "Ran FEA in ANSYS", "Skills", "ANSYS Python"):
        text.textLine(line)
    pdf.drawText(text)
    pdf.showPage()
    pdf.save()

    parsed = parse_master_resume("resume.pdf", buffer.getvalue())
    assert parsed.raw_text.strip() != ""
    assert "ansys" in parsed.raw_text.lower()


# --- Fact preservation (SPEC CORE RULE #7/#8) ------------------------------


def test_missing_keyword_reported_not_inserted() -> None:
    parsed = parse_master_resume("master_resume.md", _master_markdown_bytes())
    tailored = generate_tailored_resume(parsed, _job())

    # Keywords the master lacks evidence for are reported as missing...
    assert "kubernetes" in tailored.missing_keywords
    assert "graphql" in tailored.missing_keywords
    # ...and never appear anywhere in the generated resume.
    rendered = resume_export.to_markdown(tailored.resume).lower()
    assert "kubernetes" not in rendered
    assert "graphql" not in rendered
    # Keywords the user truthfully has are matched.
    assert "solidworks" in tailored.matched_keywords
    assert "catia" in tailored.matched_keywords


def test_verify_claims_strips_ungrounded_content() -> None:
    parsed = parse_master_resume("master_resume.md", _master_markdown_bytes())
    # Attempt to inject an ungrounded claim.
    injected = ParsedResume(
        raw_text=parsed.raw_text,
        sections={
            "skills": ["SolidWorks and ANSYS"],  # grounded -> kept
            "experience": ["Led Kubernetes migration for 10000 servers"],  # ungrounded
        },
        links=list(parsed.links),
    )
    verified, stripped = verify_claims(injected, parsed)
    assert stripped == ["Led Kubernetes migration for 10000 servers"]
    # The grounded line survived; the ungrounded one is gone.
    assert verified.sections.get("skills") == ["SolidWorks and ANSYS"]
    assert "experience" not in verified.sections


def test_is_grounded_predicate() -> None:
    parsed = parse_master_resume("master_resume.md", _master_markdown_bytes())
    assert is_grounded("SolidWorks and ANSYS", parsed) is True
    assert is_grounded("Blockchain Rust developer", parsed) is False


def test_tailoring_never_adds_tokens_outside_master() -> None:
    parsed = parse_master_resume("master_resume.md", _master_markdown_bytes())
    from jobtracker.resume.generator import master_vocabulary

    vocab = master_vocabulary(parsed)
    tailored = generate_tailored_resume(parsed, _job())
    for body in tailored.resume.sections.values():
        for line in body:
            from jobtracker.resume.generator import _content_tokens

            assert not (_content_tokens(line) - vocab)


# --- Change log + outputs --------------------------------------------------


def test_change_log_produced() -> None:
    parsed = parse_master_resume("master_resume.md", _master_markdown_bytes())
    tailored = generate_tailored_resume(parsed, _job())
    assert tailored.change_log
    assert any("mphasiz" in entry or "eorder" in entry for entry in tailored.change_log)


def test_all_three_output_formats_render() -> None:
    parsed = parse_master_resume("master_resume.md", _master_markdown_bytes())
    tailored = generate_tailored_resume(parsed, _job())

    md = resume_export.to_markdown(tailored.resume, heading="Jane Doe")
    assert "## Experience" in md

    doc = json.loads(resume_export.to_json(tailored.resume))
    assert "sections" in doc and doc["sections"]

    pdf = resume_export.to_pdf(tailored.resume, heading="Jane Doe")
    assert pdf[:4] == b"%PDF"
    assert len(pdf) > 500


# --- Service layer ---------------------------------------------------------


def _seed_job(session: Session, job_id: str = "job-1") -> None:
    if session.get(Company, "acme") is None:
        session.add(Company(id="acme", name="Acme", ats="greenhouse"))
    session.add(
        Job(
            job_id=job_id,
            company_id="acme",
            source_job_id=job_id,
            title="Mechanical Engineering Intern",
            description=_job()["description"],
            requirements=_job()["requirements"],
            preferred_qualifications=_job()["preferred_qualifications"],
            skills=_job()["skills"],
            status="active",
        )
    )
    session.flush()


def test_upload_master_preserves_original(session: Session) -> None:
    uid = auth_service.register(session, "r@example.com", "pw").id
    data = _master_markdown_bytes()
    master = resume_service.upload_master(session, uid, "master_resume.md", data)
    # Original bytes preserved untouched.
    assert resume_service.original_bytes(master) == data
    assert master.parsed_sections
    assert "experience" in (master.parsed_sections.get("sections") or {})


def test_generate_for_job_requires_saved(session: Session) -> None:
    uid = auth_service.register(session, "r2@example.com", "pw").id
    _seed_job(session)
    resume_service.upload_master(session, uid, "master_resume.md", _master_markdown_bytes())

    # Not saved yet -> error.
    try:
        resume_service.generate_for_job(session, uid, "job-1")
        raised = False
    except resume_service.ResumeServiceError:
        raised = True
    assert raised is True

    # Save it, then generation succeeds and persists a grounded draft.
    saved_job_service.save_job(session, uid, "job-1")
    outcome = resume_service.generate_for_job(session, uid, "job-1")
    row = outcome.row
    assert row.markdown and row.json_sections and row.pdf_base64
    assert resume_service.generated_pdf_bytes(row)[:4] == b"%PDF"
    assert "kubernetes" in (row.missing_keywords or [])
    assert "kubernetes" not in (row.markdown or "").lower()
    # Regeneration upserts (no duplicate rows).
    resume_service.generate_for_job(session, uid, "job-1")
    assert len(resume_service.list_generated(session, uid)) == 1
