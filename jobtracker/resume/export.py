"""Render a tailored resume to Markdown, JSON, and an ATS-friendly PDF.

All renderers are deterministic and text-based so the output is ATS-parseable.
The PDF is produced with reportlab (pure-Python, no native system libraries),
chosen deliberately to avoid WeasyPrint/Cairo/Pango system deps.

Pure functions: a :class:`~jobtracker.resume.parser.ParsedResume` in, bytes or
str out. No DB, no network, no Streamlit.
"""

from __future__ import annotations

import io
import json

from jobtracker.resume.parser import SECTION_ORDER, ParsedResume

#: Human titles for the canonical section keys.
_SECTION_TITLES: dict[str, str] = {
    "contact": "Contact",
    "summary": "Summary",
    "education": "Education",
    "experience": "Experience",
    "projects": "Projects",
    "skills": "Skills",
    "certifications": "Certifications",
    "leadership": "Leadership & Activities",
    "awards": "Awards & Honors",
    "links": "Links",
}


def _ordered_items(resume: ParsedResume) -> list[tuple[str, list[str]]]:
    """Return (section, body) pairs in the resume's own order.

    The resume preserves tailoring order; any unknown sections are appended in
    canonical order for stability.
    """
    items: list[tuple[str, list[str]]] = []
    seen: set[str] = set()
    for name, body in resume.sections.items():
        if body:
            items.append((name, body))
            seen.add(name)
    for name in SECTION_ORDER:
        if name not in seen and resume.sections.get(name):
            items.append((name, resume.sections[name]))
    return items


def _title_for(name: str) -> str:
    return _SECTION_TITLES.get(name, name.replace("_", " ").title())


def to_markdown(resume: ParsedResume, *, heading: str | None = None) -> str:
    """Render the resume as ATS-friendly Markdown."""
    lines: list[str] = []
    if heading:
        lines.append(f"# {heading}")
        lines.append("")
    for name, body in _ordered_items(resume):
        lines.append(f"## {_title_for(name)}")
        for entry in body:
            lines.append(f"- {entry}")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def to_json(resume: ParsedResume) -> str:
    """Render the resume as a structured JSON document (stable key order)."""
    payload = {
        "sections": {
            name: list(body) for name, body in _ordered_items(resume)
        },
        "links": list(resume.links),
    }
    return json.dumps(payload, indent=2, ensure_ascii=False, sort_keys=False)


def to_pdf(resume: ParsedResume, *, heading: str | None = None) -> bytes:
    """Render an ATS-friendly PDF via reportlab and return the bytes.

    Uses a simple single-column layout with standard fonts and plain text so
    ATS parsers can extract it cleanly.
    """
    from reportlab.lib.enums import TA_LEFT
    from reportlab.lib.pagesizes import letter
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
    from reportlab.lib.units import inch
    from reportlab.platypus import (
        Paragraph,
        SimpleDocTemplate,
        Spacer,
    )
    from reportlab.platypus.flowables import HRFlowable

    buffer = io.BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=letter,
        leftMargin=0.75 * inch,
        rightMargin=0.75 * inch,
        topMargin=0.75 * inch,
        bottomMargin=0.75 * inch,
        title=heading or "Resume",
    )

    base = getSampleStyleSheet()
    name_style = ParagraphStyle(
        "ResumeName",
        parent=base["Title"],
        fontName="Helvetica-Bold",
        fontSize=18,
        spaceAfter=6,
        alignment=TA_LEFT,
    )
    section_style = ParagraphStyle(
        "ResumeSection",
        parent=base["Heading2"],
        fontName="Helvetica-Bold",
        fontSize=12,
        spaceBefore=10,
        spaceAfter=2,
        textColor="#000000",
    )
    body_style = ParagraphStyle(
        "ResumeBody",
        parent=base["BodyText"],
        fontName="Helvetica",
        fontSize=10,
        leading=13,
        spaceAfter=2,
        bulletIndent=6,
        leftIndent=14,
    )

    story: list = []
    if heading:
        story.append(Paragraph(_escape(heading), name_style))

    for name, body in _ordered_items(resume):
        story.append(Paragraph(_escape(_title_for(name)), section_style))
        story.append(HRFlowable(width="100%", thickness=0.5, spaceAfter=4))
        for entry in body:
            story.append(Paragraph(f"• {_escape(entry)}", body_style))
        story.append(Spacer(1, 4))

    if not story:
        story.append(Paragraph("(empty resume)", body_style))

    doc.build(story)
    return buffer.getvalue()


def _escape(text: str) -> str:
    """Escape reportlab/XML-sensitive characters in paragraph text."""
    return (
        (text or "")
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )
