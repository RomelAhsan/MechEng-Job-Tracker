"""Tailored resume generation with strict fact preservation (SPEC Phase 4).

This module REORDERS and EMPHASIZES content that already exists in the master
resume to match a target job. It NEVER invents experience, skills, dates,
employers, schools, tools, or metrics. The authoritative guard is
:func:`verify_claims`, a deterministic verifier that checks every token in the
generated resume is grounded in the master resume and strips/flags anything
that is not (SPEC CORE RULE #7/#8, ACCURACY check).

Outputs:

* a tailored :class:`~jobtracker.resume.parser.ParsedResume` (reordered /
  emphasized, still 100% grounded),
* a *change log* describing what was reordered/emphasized,
* a *missing-keywords report* of job keywords the user lacks evidence for
  (suggestions only - NEVER inserted into the resume).

Every public function here is pure (no DB, no network, no Streamlit). The
service layer may optionally consult an AI provider for emphasis hints, but the
verifier runs regardless so AI can never introduce fabrications.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from jobtracker.resume.parser import SECTION_ORDER, ParsedResume

#: Tokens ignored when computing keyword overlap / grounding. Short function
#: words carry no evidentiary weight and would create false "matches".
_STOPWORDS: frozenset[str] = frozenset(
    {
        "the", "and", "for", "with", "you", "your", "our", "are", "will", "has",
        "have", "that", "this", "from", "into", "a", "an", "to", "of", "in",
        "on", "at", "by", "or", "as", "is", "be", "we", "us", "it", "its",
        "their", "they", "them", "who", "what", "which", "work", "working",
        "role", "team", "job", "position", "ability", "able", "strong", "good",
        "experience", "experienced", "skills", "skill", "year", "years",
        "etc", "including", "include", "includes", "plus", "preferred",
        "required", "requirements", "responsibilities", "duties", "candidate",
        "looking", "seeking", "join", "help", "across", "within", "using",
    }
)

#: Minimum token length considered a meaningful keyword.
_MIN_TOKEN_LEN = 3

_WORD_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9+#./\-]*")


@dataclass
class TailoredResume:
    """Result of tailoring a master resume to a specific job.

    ``resume`` is the reordered/emphasized (and verified-grounded) resume.
    ``change_log`` lists human-readable changes. ``missing_keywords`` are job
    keywords the master lacks evidence for (suggestions only). ``matched_keywords``
    are job keywords the user truthfully has. ``stripped`` records any content
    removed by the verifier because it was not grounded (normally empty).
    """

    resume: ParsedResume
    change_log: list[str] = field(default_factory=list)
    missing_keywords: list[str] = field(default_factory=list)
    matched_keywords: list[str] = field(default_factory=list)
    stripped: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "resume": self.resume.to_dict(),
            "change_log": list(self.change_log),
            "missing_keywords": list(self.missing_keywords),
            "matched_keywords": list(self.matched_keywords),
            "stripped": list(self.stripped),
        }


def tokenize(text: str) -> list[str]:
    """Lowercase word tokens (keeping ``+ # . / -`` for tools like C++/CI/CD).

    Leading/trailing separator punctuation is trimmed so sentence punctuation
    (e.g. a trailing period in ``Python.``) does not create a distinct token,
    while internal separators (``c++``, ``ci/cd``, ``gd&t``) are preserved.
    """
    tokens: list[str] = []
    for m in _WORD_RE.finditer(text or ""):
        tok = m.group(0).lower().strip("./-")
        if tok:
            tokens.append(tok)
    return tokens


def _content_tokens(text: str) -> set[str]:
    """Meaningful tokens for grounding/overlap (stopwords + shorts removed)."""
    tokens: set[str] = set()
    for tok in tokenize(text):
        bare = tok.strip("+#./-")
        if len(tok) < _MIN_TOKEN_LEN:
            continue
        if tok in _STOPWORDS or bare in _STOPWORDS:
            continue
        tokens.add(tok)
    return tokens


def master_vocabulary(master: ParsedResume) -> set[str]:
    """Return the full set of grounded tokens present in the master resume.

    Every claim the generated resume makes MUST be a subset of this vocabulary.
    """
    return set(tokenize(master.raw_text)) | {
        tok
        for body in master.sections.values()
        for line in body
        for tok in tokenize(line)
    }


def job_keywords(job: dict) -> list[str]:
    """Extract de-duplicated, ranked keywords from a job dict.

    Looks at title, description, requirements, preferred_qualifications, and
    skills. Order reflects first appearance so the report is stable.
    """
    parts: list[str] = []
    for key in ("title", "description"):
        value = job.get(key)
        if value:
            parts.append(str(value))
    for key in ("requirements", "preferred_qualifications", "skills"):
        value = job.get(key) or []
        if isinstance(value, str):
            parts.append(value)
        else:
            parts.extend(str(v) for v in value)

    seen: list[str] = []
    seen_set: set[str] = set()
    for chunk in parts:
        for tok in _content_tokens(chunk):
            if tok not in seen_set:
                seen_set.add(tok)
                seen.append(tok)
    return seen


def _line_relevance(line: str, keyword_set: set[str]) -> int:
    """Number of distinct job keywords a resume line evidences."""
    return len(_content_tokens(line) & keyword_set)


def _reorder_section(body: list[str], keyword_set: set[str]) -> tuple[list[str], bool]:
    """Stable-sort a section's lines by job relevance (desc). Returns changed."""
    indexed = list(enumerate(body))
    reordered = sorted(
        indexed,
        key=lambda pair: (-_line_relevance(pair[1], keyword_set), pair[0]),
    )
    new_body = [line for _, line in reordered]
    changed = new_body != body
    return new_body, changed


def _section_relevance(body: list[str], keyword_set: set[str]) -> int:
    return sum(_line_relevance(line, keyword_set) for line in body)


def generate_tailored_resume(master: ParsedResume, job: dict) -> TailoredResume:
    """Reorder/emphasize the master resume for ``job`` without fabricating.

    Steps:

    1. Compute job keywords and which ones the master truthfully evidences
       (``matched``) vs. lacks (``missing`` - suggestions only).
    2. Reorder experience/projects/skills bullets so the most job-relevant,
       already-present lines come first (emphasis without new content).
    3. Reorder the sections themselves so the most relevant sections surface
       earlier, while keeping ``contact``/``summary`` pinned at the top.
    4. Run :func:`verify_claims` to strip anything not grounded in the master
       (defense in depth; reordering alone cannot add content).

    Returns a :class:`TailoredResume` with the verified resume, change log,
    and missing-keyword report.
    """
    keywords = job_keywords(job)
    keyword_set = set(keywords)
    vocab = master_vocabulary(master)

    matched = [kw for kw in keywords if kw in vocab]
    missing = [kw for kw in keywords if kw not in vocab]

    change_log: list[str] = []
    new_sections: dict[str, list[str]] = {}

    # 2. Reorder bullets within emphasis-eligible sections.
    emphasis_sections = {"experience", "projects", "skills", "leadership", "awards"}
    for name, body in master.sections.items():
        if name in emphasis_sections and len(body) > 1:
            reordered, changed = _reorder_section(body, keyword_set)
            new_sections[name] = reordered
            if changed:
                change_log.append(
                    f"Reordered '{name}' to emphasize {len(matched)} matching "
                    f"keyword(s) the resume already evidences."
                )
        else:
            new_sections[name] = list(body)

    # 3. Reorder sections by aggregate relevance, pinning contact/summary.
    pinned = [n for n in ("contact", "summary") if n in new_sections]
    movable = [n for n in new_sections if n not in pinned]
    canonical_index = {name: i for i, name in enumerate(SECTION_ORDER)}
    movable_sorted = sorted(
        movable,
        key=lambda n: (
            -_section_relevance(new_sections[n], keyword_set),
            canonical_index.get(n, len(SECTION_ORDER)),
        ),
    )
    final_order = pinned + movable_sorted
    if movable_sorted != movable:
        top = movable_sorted[0] if movable_sorted else ""
        change_log.append(
            f"Reordered sections to surface the most job-relevant section "
            f"first ('{top}')."
        )

    ordered_sections = {name: new_sections[name] for name in final_order}
    tailored = ParsedResume(
        raw_text=master.raw_text,
        sections=ordered_sections,
        links=list(master.links),
    )

    # 4. Authoritative fact-preservation pass.
    tailored, stripped = verify_claims(tailored, master)
    if stripped:
        change_log.append(
            f"Removed {len(stripped)} line(s) not grounded in the master resume."
        )

    if matched:
        change_log.append(
            "Emphasized evidenced keywords: " + ", ".join(sorted(matched)[:15])
        )
    if not change_log:
        change_log.append("No reordering needed; resume already matched the job.")

    return TailoredResume(
        resume=tailored,
        change_log=change_log,
        missing_keywords=missing,
        matched_keywords=matched,
        stripped=stripped,
    )


def verify_claims(
    generated: ParsedResume, master: ParsedResume
) -> tuple[ParsedResume, list[str]]:
    """Strip any generated line whose content is not grounded in the master.

    The authoritative, deterministic fact-preservation check (SPEC CORE RULE
    #7/#8). Every meaningful token in a generated line MUST appear in the
    master resume's vocabulary; a line carrying any ungrounded content token is
    removed and recorded in the returned ``stripped`` list. Lines with no
    meaningful tokens (pure punctuation) are kept as-is.

    Returns ``(verified_resume, stripped_lines)``. The returned resume is a new
    object; inputs are not mutated.
    """
    vocab = master_vocabulary(master)
    clean_sections: dict[str, list[str]] = {}
    stripped: list[str] = []

    for name, body in generated.sections.items():
        kept: list[str] = []
        for line in body:
            ungrounded = _content_tokens(line) - vocab
            if ungrounded:
                stripped.append(line)
                continue
            kept.append(line)
        if kept:
            clean_sections[name] = kept

    verified = ParsedResume(
        raw_text=master.raw_text,
        sections=clean_sections,
        links=[link for link in generated.links if link in master.links],
    )
    return verified, stripped


def is_grounded(claim: str, master: ParsedResume) -> bool:
    """Return True if every meaningful token in ``claim`` exists in the master.

    Convenience predicate used by callers/tests to check a single claim before
    it would ever be added (it never is added automatically).
    """
    return not (_content_tokens(claim) - master_vocabulary(master))
