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
    country: str | None = None


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
            "failed_rules": (
                "array of the RULE numbers (1-7) this posting fails; [] if none. "
                "Decide this FIRST, rule by rule"
            ),
            "eligible": "boolean: true exactly when failed_rules is []",
            "recommendation": "apply (eligible) or skip (not eligible)",
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
#: Bumped whenever the rules below change, so cached verdicts made under older
#: rules are re-done instead of being trusted (see ai_service._find_cache).
PROMPT_VERSION = 5

ELIGIBILITY_RULES = (
    "=== YOUR JOB ===\n"
    "These postings already passed a keyword filter but are AMBIGUOUS. Decide, "
    "for this specific student, whether they can actually apply and be hired. "
    "Your answer must be correct: a wrong 'eligible' shows the student a job "
    "they cannot get; a wrong 'not eligible' hides one they could.\n"
    "Set \"eligible\": false and recommendation=\"skip\" the moment there is ANY "
    "concrete sign the student is ineligible. Missing information is NOT a "
    "sign of ineligibility. Use recommendation=\"apply\" with "
    "\"eligible\": true when nothing disqualifies the student.\n"
    "=== THE STUDENT ===\n"
    "First-year undergraduate in Mechanical Engineering at the University of "
    "Waterloo. CANADIAN citizen only: no US citizenship, no US green card, no "
    "security clearance, no work permit for any other country. Has NOT "
    "completed any degree. Seeks a 4-month co-op/internship in any term.\n"
    "=== RULES ===\n"
    "1. It must be a student role (co-op / internship / student position). A "
    "regular full-time or permanent job is not eligible, even if junior.\n"
    "2. CITIZENSHIP: not eligible if the posting requires US citizenship, US "
    "person status, a green card, a security clearance, or ITAR/EAR export-"
    "control eligibility. For a role located in the United States at an "
    "aerospace, defense, space, satellite, rocket, or weapons employer, treat "
    "silence as 'US citizenship required' (ITAR) unless the posting explicitly "
    "welcomes international/Canadian applicants or offers sponsorship. A role "
    "located in Canada is fine on this rule.\n"
    "3. OTHER US ROLES: a US internship can be open to Canadian students "
    "(J-1). Eligible only if the posting does not restrict it to US work "
    "authorization; 'must be authorized to work in the US' or 'no sponsorship' "
    "means not eligible. Silence about sponsorship or J-1 at a NON-aerospace/"
    "defense US employer is NOT a reason to mark it ineligible.\n"
    "4. OTHER COUNTRIES: a role located outside Canada and the US is not "
    "eligible.\n"
    "5. LEVEL: not eligible if it requires a completed degree (e.g. 'Bachelor's "
    "degree in ...' as a requirement, not 'pursuing'), a Master's/PhD/MBA, "
    "graduate-student status, 'new grad', final-year/upper-year-only students, "
    "seniority, or years of professional experience.\n"
    "6. TERM: the student can work ANY 4-month term (Jan-Apr, May-Aug, or "
    "Sep-Dec), so the season never disqualifies. Not eligible only if the "
    "posting requires a duration that cannot be 4 months (e.g. 8 or 12 months "
    "only, or 'full year').\n"
    "7. FIELD: eligible if the work is mechanical, mechatronics, robotics, "
    "manufacturing, hardware, aerospace/mechanical, automotive, thermal, "
    "energy/power-plant, or product-design engineering. Not eligible for pure "
    "software, electrical-only, finance, HR, sales, or non-engineering roles.\n"
    "8. Quote the exact posting text that decided it in \"evidence\". Never "
    "invent facts not in the posting.\n"
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
        f"Country: {job.country or 'unknown'}\n"
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
        f"Country: {job.country or 'unknown'}\n"
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
