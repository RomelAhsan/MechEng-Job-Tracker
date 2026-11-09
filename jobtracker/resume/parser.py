"""Master-resume parsing (SPEC Phase 4).

Pure functions: given an uploaded filename and its raw bytes, extract the plain
text (via pypdf for PDF, python-docx for DOCX, or a plain decode for Markdown /
text) and segment it into the SPEC sections using robust heading heuristics.

The ORIGINAL bytes are never mutated here; this module only reads them. The
caller (``resume_service``) is responsible for persisting the untouched
original alongside the parsed structure.

SPEC sections (CORE RULE #7/#8 fact preservation starts here): ``contact``,
``education``, ``experience``, ``projects``, ``skills``, ``certifications``,
``leadership``, ``awards``, ``links``. Any text that cannot be attributed to a
heading is kept under ``summary`` so nothing from the master resume is lost.
"""

from __future__ import annotations

import io
import re
from dataclasses import dataclass, field

#: Canonical SPEC sections, in display order. ``summary`` holds unlabeled
#: leading content so no master-resume text is ever dropped.
SECTION_ORDER: tuple[str, ...] = (
    "contact",
    "summary",
    "education",
    "experience",
    "projects",
    "skills",
    "certifications",
    "leadership",
    "awards",
    "links",
)

#: Heading aliases -> canonical section. Matching is case-insensitive and
#: tolerant of trailing punctuation/colons.
_HEADING_ALIASES: dict[str, str] = {
    "contact": "contact",
    "contact information": "contact",
    "summary": "summary",
    "professional summary": "summary",
    "objective": "summary",
    "profile": "summary",
    "about": "summary",
    "education": "education",
    "academic background": "education",
    "experience": "experience",
    "work experience": "experience",
    "professional experience": "experience",
    "employment": "experience",
    "employment history": "experience",
    "work history": "experience",
    "projects": "projects",
    "personal projects": "projects",
    "technical projects": "projects",
    "project experience": "projects",
    "skills": "skills",
    "technical skills": "skills",
    "core competencies": "skills",
    "competencies": "skills",
    "tools": "skills",
    "technologies": "skills",
    "certifications": "certifications",
    "certification": "certifications",
    "licenses": "certifications",
    "licenses & certifications": "certifications",
    "leadership": "leadership",
    "leadership & activities": "leadership",
    "activities": "leadership",
    "involvement": "leadership",
    "volunteering": "leadership",
    "awards": "awards",
    "honors": "awards",
    "honors & awards": "awards",
    "achievements": "awards",
    "links": "links",
    "websites": "links",
    "portfolio": "links",
}

_EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
_PHONE_RE = re.compile(r"(?:\+?\d[\d\-.\s()]{7,}\d)")
_URL_RE = re.compile(r"(?:https?://|www\.)\S+", re.IGNORECASE)
#: A Markdown ATX heading (``# Heading``) or a short all-caps/title heading line.
_MD_HEADING_RE = re.compile(r"^\s{0,3}#{1,6}\s*(.+?)\s*#*\s*$")


@dataclass
class ParsedResume:
    """Structured master-resume content extracted from an upload.

    ``sections`` maps a canonical SPEC section name to a list of text lines
    (bullets/paragraphs) found under it. ``links`` is a de-duplicated list of
    URLs discovered anywhere in the document. ``raw_text`` is the full extracted
    text (never the original bytes).
    """

    raw_text: str
    sections: dict[str, list[str]] = field(default_factory=dict)
    links: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "sections": {k: list(v) for k, v in self.sections.items()},
            "links": list(self.links),
            "raw_text": self.raw_text,
        }

    @classmethod
    def from_dict(cls, data: dict) -> ParsedResume:
        sections = {
            str(k): [str(x) for x in (v or [])]
            for k, v in (data.get("sections") or {}).items()
        }
        return cls(
            raw_text=str(data.get("raw_text", "")),
            sections=sections,
            links=[str(x) for x in (data.get("links") or [])],
        )


class ResumeParseError(ValueError):
    """Raised when the uploaded file cannot be decoded/extracted."""


def extract_text(filename: str, data: bytes) -> str:
    """Return the plain text of an uploaded resume without mutating ``data``.

    Dispatches on the file extension: ``.pdf`` via pypdf, ``.docx`` via
    python-docx, everything else (``.md``, ``.markdown``, ``.txt`` or unknown)
    via a tolerant UTF-8 decode. Raises :class:`ResumeParseError` on failure.
    """
    suffix = _suffix(filename)
    if suffix == ".pdf":
        return _extract_pdf(data)
    if suffix == ".docx":
        return _extract_docx(data)
    # Markdown / plain text / unknown: decode leniently.
    try:
        return data.decode("utf-8", errors="replace")
    except Exception as exc:  # noqa: BLE001 - surface a clean parse error
        raise ResumeParseError(f"Could not decode resume text: {exc}") from exc


def _suffix(filename: str) -> str:
    name = (filename or "").strip().lower()
    dot = name.rfind(".")
    return name[dot:] if dot != -1 else ""


def _extract_pdf(data: bytes) -> str:
    from pypdf import PdfReader
    from pypdf.errors import PdfReadError

    try:
        reader = PdfReader(io.BytesIO(data))
        pages = [page.extract_text() or "" for page in reader.pages]
    except (PdfReadError, OSError, ValueError) as exc:
        raise ResumeParseError(f"Could not read PDF resume: {exc}") from exc
    return "\n".join(pages)


def _extract_docx(data: bytes) -> str:
    import docx

    try:
        document = docx.Document(io.BytesIO(data))
    except Exception as exc:  # noqa: BLE001 - python-docx raises broad errors
        raise ResumeParseError(f"Could not read DOCX resume: {exc}") from exc
    return "\n".join(p.text for p in document.paragraphs)


def _normalize_heading(line: str) -> str | None:
    """Return the canonical section for ``line`` if it looks like a heading."""
    stripped = line.strip()
    if not stripped:
        return None

    candidate = stripped
    md = _MD_HEADING_RE.match(line)
    if md:
        candidate = md.group(1).strip()

    key = candidate.strip().strip(":").strip().lower()
    # Strip surrounding markdown emphasis markers (**Heading**, __Heading__).
    key = key.strip("*_ ").strip()
    if key in _HEADING_ALIASES:
        return _HEADING_ALIASES[key]

    # Heuristic for unmarked headings: a short, title/upper-case line with no
    # terminal sentence punctuation that matches a known alias.
    if md is None:
        is_short = len(candidate) <= 40 and len(candidate.split()) <= 4
        looks_like_heading = candidate.isupper() or candidate.istitle()
        no_sentence = not candidate.endswith((".", ",", ";"))
        if is_short and looks_like_heading and no_sentence and key in _HEADING_ALIASES:
            return _HEADING_ALIASES[key]
    return None


def _clean_bullet(line: str) -> str:
    """Strip common bullet/list markers so stored lines are normalized."""
    text = line.strip()
    text = re.sub(r"^[-*•·‣◦]+\s*", "", text)
    text = re.sub(r"^\d+[.)]\s*", "", text)
    return text.strip()


def _extract_contact(lines: list[str]) -> list[str]:
    """Pull email/phone/url contact tokens from the top of the document."""
    found: list[str] = []
    for line in lines[:8]:
        for match in _EMAIL_RE.findall(line):
            if match not in found:
                found.append(match)
        for match in _PHONE_RE.findall(line):
            token = match.strip()
            if len(re.sub(r"\D", "", token)) >= 10 and token not in found:
                found.append(token)
    return found


def _collect_links(text: str) -> list[str]:
    links: list[str] = []
    for match in _URL_RE.findall(text):
        url = match.rstrip(".,);]")
        if url not in links:
            links.append(url)
    return links


def segment_sections(text: str) -> ParsedResume:
    """Segment extracted text into SPEC sections via heading heuristics.

    Lines before the first recognized heading are kept under ``summary`` so no
    content is lost. Contact tokens and links are additionally harvested into
    their own sections. Pure function: identical input yields identical output.
    """
    raw_lines = text.splitlines()
    sections: dict[str, list[str]] = {}
    current = "summary"

    for raw in raw_lines:
        heading = _normalize_heading(raw)
        if heading is not None:
            current = heading
            sections.setdefault(current, [])
            continue
        cleaned = _clean_bullet(raw)
        if cleaned:
            sections.setdefault(current, []).append(cleaned)

    # Harvest contact + links across the whole document (non-destructive).
    contact = _extract_contact(raw_lines)
    if contact:
        existing = sections.setdefault("contact", [])
        for token in contact:
            if token not in existing:
                existing.append(token)

    links = _collect_links(text)
    if links:
        existing_links = sections.setdefault("links", [])
        for url in links:
            if url not in existing_links:
                existing_links.append(url)

    # Drop an empty summary bucket if nothing landed there.
    if "summary" in sections and not sections["summary"]:
        del sections["summary"]

    ordered = {
        name: sections[name] for name in SECTION_ORDER if sections.get(name)
    }
    # Preserve any unexpected sections (defensive; none expected today).
    for name, body in sections.items():
        if name not in ordered and body:
            ordered[name] = body

    return ParsedResume(raw_text=text, sections=ordered, links=links)


def parse_master_resume(filename: str, data: bytes) -> ParsedResume:
    """Parse an uploaded master resume into structured SPEC sections.

    Pure function (bytes in, structure out). The original ``data`` is read but
    never modified; the caller persists it untouched.
    """
    text = extract_text(filename, data)
    return segment_sections(text)
