"""Prompt templates for the AI relevance engine (SPEC Phase 3).

Builds the strict-JSON prompt sent to the model. The prompt includes the user
profile, the job's structured fields, and the CLEANED description text that was
already stored on ``Job.description`` by the normalizer - raw HTML is NEVER
sent. Overly long descriptions are truncated to keep prompts bounded.
"""

from __future__ import annotations

import json
from dataclasses import dataclass

#: Exact SPEC output fields the model must return (and only these).
SPEC_FIELDS: tuple[str, ...] = (
    "relevance_score",
    "recommendation",
    "mechanical_relevance",
    "term_assessment",
    "duration_assessment",
    "location_assessment",
    "work_authorization_assessment",
    "matched_skills",
    "missing_skills",
    "concerns",
    "evidence",
    "explanation",
    "confidence",
)

#: Maximum characters of job description included in the prompt.
MAX_DESCRIPTION_CHARS = 8000


@dataclass
class JobPromptInput:
    """Structured, already-cleaned job fields used to build the prompt.

    ``description`` must be the cleaned text stored on ``Job.description`` - the
    caller never passes raw HTML.
    """

    title: str
    company: str
    location: str
    term_season: str
    duration_label: str
    citizenship_requirement: str
    work_authorization_requirement: str
    visa_sponsorship: str
    sponsorship_evidence: str
    description: str
    requirements: list[str]
    preferred_qualifications: list[str]
    skills: list[str]
    application_url: str


def truncate_description(text: str, *, limit: int = MAX_DESCRIPTION_CHARS) -> str:
    """Truncate overly long descriptions, appending a marker when cut."""
    text = text or ""
    if len(text) <= limit:
        return text
    return text[:limit].rstrip() + "\n[... truncated ...]"


def _schema_block() -> str:
    """Human-readable description of the required strict-JSON schema."""
    return json.dumps(
        {
            "relevance_score": "integer 0-100",
            "recommendation": "one of: apply | maybe | skip",
            "mechanical_relevance": "short string assessing mechanical-engineering fit",
            "term_assessment": "short string on term/season fit vs the profile",
            "duration_assessment": "short string on co-op duration fit",
            "location_assessment": "short string on location fit",
            "work_authorization_assessment": (
                "short string on citizenship/sponsorship fit"
            ),
            "matched_skills": "array of strings from the profile that match",
            "missing_skills": "array of strings the role wants that are missing",
            "concerns": "array of short strings describing risks/caveats",
            "evidence": "array of short quoted snippets supporting the assessment",
            "explanation": "2-4 sentence overall rationale",
            "confidence": "number 0.0-1.0",
        },
        indent=2,
    )


def build_profile_summary(profile: dict) -> str:
    """Render a compact user-profile summary block for the prompt."""
    fields = (
        ("Name", profile.get("name")),
        ("Program", profile.get("program")),
        ("University", profile.get("university")),
        ("Year", profile.get("year")),
        ("Citizenship", profile.get("citizenship")),
        ("Work authorization", profile.get("work_authorization")),
        ("Preferred locations", profile.get("preferred_locations")),
        ("Available terms", profile.get("available_terms")),
        ("Preferred duration (months)", profile.get("preferred_duration_months")),
        ("Interests", profile.get("interests")),
        ("Skills", profile.get("skills")),
    )
    lines = []
    for label, value in fields:
        if value in (None, "", [], {}):
            continue
        if isinstance(value, list):
            value = ", ".join(str(v) for v in value)
        lines.append(f"- {label}: {value}")
    return "\n".join(lines) or "- (no profile details provided)"


#: The hard eligibility rules the model MUST apply. These make the AI verdict
#: rule-governed and trustworthy (not a vague "fit" impression): the student is
#: a FIRST-YEAR CANADIAN CITIZEN seeking a 4-month Spring co-op/internship, and
#: the rules below define exactly when to answer "skip". The deterministic
#: Stage-1 filter already removed the clear-cut cases; the model's job is the
#: AMBIGUOUS ones, and it must resolve ambiguity conservatively.
ELIGIBILITY_RULES = (
    "=== ELIGIBILITY RULES (apply strictly; set recommendation=\"skip\" if ANY "
    "fail) ===\n"
    "The student is a FIRST-YEAR undergraduate and a CANADIAN CITIZEN (no US "
    "citizenship, no US work authorization, no completed degree).\n"
    "1. CITIZENSHIP / ITAR (most common disqualifier -- apply firmly): If the "
    "role is in the United States (or its country is unknown) AND the employer "
    "is in aerospace, defense, space, satellites, rockets/propulsion, weapons, "
    "or any ITAR/EAR/export-controlled field (e.g. SpaceX, Blue Origin, "
    "Lockheed, Northrop, Boeing, RTX, Raytheon, Anduril and similar), you MUST "
    "assume it requires US citizenship or permanent residency / a security "
    "clearance that a Canadian citizen cannot obtain -> recommendation=skip. "
    "This holds EVEN IF the posting never mentions citizenship, ITAR, or "
    "clearance -- treat silence as 'US citizenship required' for these "
    "employers. The ONLY exception is a posting that EXPLICITLY offers visa "
    "sponsorship or explicitly welcomes non-US / international / Canadian "
    "applicants. A role physically located in CANADA is fine on this axis.\n"
    "2. US (non-defense) roles are eligible ONLY if sponsorship is explicitly "
    "available or the posting welcomes international/Canadian applicants; if a "
    "US role is silent on sponsorship, lean skip (a first-year Canadian rarely "
    "gets US sponsorship).\n"
    "3. LEVEL: skip if the role requires a completed degree, a Master's/PhD, "
    "'new grad'/'graduate' status, seniority (senior/staff/principal/lead), or "
    "multiple years of professional experience. Co-op/internship/student roles "
    "open to any year are eligible.\n"
    "4. FIELD: eligible only if mechanical / mechatronics / robotics / "
    "manufacturing / hardware / aerospace-mechanical / automotive / thermal / "
    "product-design engineering. Skip pure software/EE/finance/HR/sales roles.\n"
    "5. When genuinely ambiguous after the above, prefer 'maybe' over a "
    "confident wrong answer, and explain the uncertainty. NEVER guess facts not "
    "in the posting. Accuracy matters more than inclusion.\n"
)


def build_analysis_prompt(profile: dict, job: JobPromptInput) -> str:
    """Build the strict-JSON analysis prompt for one job.

    The description is the cleaned text already stored on the Job row; this
    function truncates it but never receives or emits raw HTML.
    """
    description = truncate_description(job.description)
    requirements = "\n".join(f"- {r}" for r in job.requirements) or "- (none listed)"
    preferred = (
        "\n".join(f"- {r}" for r in job.preferred_qualifications)
        or "- (none listed)"
    )
    skills = ", ".join(job.skills) or "(none listed)"

    return (
        "You are a co-op job relevance assistant for a University of Waterloo "
        "Mechanical Engineering student. Assess how well the job below fits the "
        "student's profile.\n\n"
        "Return STRICT JSON ONLY (no markdown, no code fences, no prose before "
        "or after). The JSON object MUST contain EXACTLY these keys and nothing "
        "else:\n"
        f"{_schema_block()}\n\n"
        "Rules: never invent requirements; base evidence on the provided text; "
        "if something is unknown say so in the relevant assessment and lower the "
        "confidence; recommendation must be one of apply, maybe, or skip.\n\n"
        f"{ELIGIBILITY_RULES}\n"
        "=== STUDENT PROFILE ===\n"
        f"{build_profile_summary(profile)}\n\n"
        "=== JOB ===\n"
        f"Title: {job.title}\n"
        f"Company: {job.company}\n"
        f"Location: {job.location}\n"
        f"Term/Season: {job.term_season}\n"
        f"Duration: {job.duration_label}\n"
        f"Citizenship requirement: {job.citizenship_requirement or 'unknown'}\n"
        f"Work authorization: {job.work_authorization_requirement or 'unknown'}\n"
        f"Visa sponsorship: {job.visa_sponsorship or 'unknown'}\n"
        f"Sponsorship evidence: {job.sponsorship_evidence or '(none)'}\n"
        f"Application URL: {job.application_url}\n"
        f"Skills (parsed): {skills}\n\n"
        "Requirements:\n"
        f"{requirements}\n\n"
        "Preferred qualifications:\n"
        f"{preferred}\n\n"
        "Description (cleaned text):\n"
        f"{description}\n"
    )


#: Characters of each job's description included in a BATCH prompt. Much shorter
#: than the single-job limit so many jobs fit in one request without blowing the
#: token budget -- eligibility (term/duration/degree/experience/citizenship/
#: mechanical-fit) is decided from the title + the opening of the description,
#: which this comfortably covers.
MAX_BATCH_DESCRIPTION_CHARS = 1500


def _batch_job_block(index: int, job: JobPromptInput) -> str:
    """Compact per-job block for a batch prompt, tagged with a stable index."""
    description = truncate_description(
        job.description, limit=MAX_BATCH_DESCRIPTION_CHARS
    )
    skills = ", ".join(job.skills) or "(none listed)"
    reqs = "; ".join(job.requirements[:8]) or "(none listed)"
    return (
        f"--- JOB {index} ---\n"
        f"Title: {job.title}\n"
        f"Company: {job.company}\n"
        f"Location: {job.location}\n"
        f"Term/Season: {job.term_season}\n"
        f"Duration: {job.duration_label}\n"
        f"Citizenship requirement: {job.citizenship_requirement or 'unknown'}\n"
        f"Visa sponsorship: {job.visa_sponsorship or 'unknown'}\n"
        f"Skills: {skills}\n"
        f"Requirements: {reqs}\n"
        f"Description: {description}\n"
    )


def build_batch_analysis_prompt(
    profile: dict, jobs: list[JobPromptInput]
) -> str:
    """Build ONE prompt that asks the model to assess MANY jobs at once.

    This is the efficiency path: instead of one request per job (which exhausts
    the free-tier daily request quota), a single request carries a batch of
    jobs and the model returns a JSON ARRAY of per-job verdicts, each tagged
    with the integer ``index`` it was given so the caller can map results back
    to jobs reliably even if the model reorders or drops one.

    The required per-job object uses the SAME SPEC fields as the single-job
    prompt, plus an ``index`` echoing the job's number.
    """
    blocks = "\n".join(_batch_job_block(i, j) for i, j in enumerate(jobs))
    return (
        "You are a co-op job relevance assistant for a University of Waterloo "
        "Mechanical Engineering student. Assess how well EACH job below fits "
        "the student's profile.\n\n"
        "Return STRICT JSON ONLY: a JSON ARRAY with ONE object per job, no "
        "markdown, no code fences, no prose before or after. Each object MUST "
        "contain an integer \"index\" (echoing the JOB number it refers to) "
        "plus EXACTLY these keys:\n"
        f"{_schema_block()}\n\n"
        "Return exactly one object for every job index provided. Rules: never "
        "invent requirements; base evidence on the provided text; if something "
        "is unknown say so and lower the confidence; recommendation must be one "
        "of apply, maybe, or skip.\n\n"
        f"{ELIGIBILITY_RULES}\n"
        "=== STUDENT PROFILE ===\n"
        f"{build_profile_summary(profile)}\n\n"
        "=== JOBS ===\n"
        f"{blocks}\n"
    )
