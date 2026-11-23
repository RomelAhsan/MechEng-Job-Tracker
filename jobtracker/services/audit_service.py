"""AI spot-check of jobs the screening RULES hid.

The rules (term, degree, experience, citizenship, ...) are deterministic and
fast, but a parsing mistake can hide a job the student could apply to. A small
sample of rule-hidden student roles is sent to the AI with the normal
eligibility prompt. When the AI thinks the student IS eligible, the hidden-jobs
list on the website flags the job as worth a second look.

This never changes what is shown: the rule decision stands until the rules
are fixed. The sample is small and spaced out so the free AI quota is kept for
the main screening pass.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from jobtracker.ai.prompts import PROMPT_VERSION, build_batch_analysis_prompt
from jobtracker.ai.provider import AIProvider, AIProviderError, NullProvider, get_provider
from jobtracker.db.models import Company, Job
from jobtracker.services import dashboard_service
from jobtracker.settings import Settings, get_settings

logger = logging.getLogger("jobtracker.services.audit")

#: Rule decisions that are user policy, not text parsing, so auditing them
#: only adds noise (the user is not eligible for any US defense role).
_NOT_AUDITED = {"not_student_role", "foreign_country", "itar_company", "removed"}


def audit_candidates(session: Session, *, limit: int) -> list[tuple[Job, str]]:
    """Rule-hidden student roles in Canada/US that have no current audit."""
    relevant = list(dashboard_service.RELEVANT_CLASSIFICATIONS)
    rows = session.execute(
        select(Job, Company.name)
        .join(Company, Company.id == Job.company_id)
        .where(
            Job.status == "active",
            Job.eligible.is_(False),
            Job.classification.in_(relevant),
            Company.enabled.is_(True),
        )
        .order_by(Job.first_seen_at.desc())
    ).all()
    out: list[tuple[Job, str]] = []
    for job, name in rows:
        failed = set(job.eligibility_failed_rules or [])
        if not failed or failed & _NOT_AUDITED:
            continue
        if (job.country or "").lower() not in ("", "canada", "united states"):
            continue
        audit = job.rule_audit if isinstance(job.rule_audit, dict) else None
        if (
            audit
            and audit.get("_prompt_version") == PROMPT_VERSION
            and sorted(audit.get("rules") or []) == sorted(failed)
        ):
            continue
        out.append((job, name))
        if len(out) >= limit:
            break
    return out


def audit_rejected_jobs(
    session: Session,
    *,
    limit: int = 12,
    settings: Settings | None = None,
    provider: AIProvider | None = None,
) -> int:
    """Audit up to ``limit`` rule-hidden jobs in one AI request; return count."""
    from jobtracker.screen.ai_verdict import ai_eligibility
    from jobtracker.services.ai_service import (
        _job_prompt_input,
        parse_strict_json_array,
        resolve_profile,
    )

    settings = settings or get_settings()
    provider = provider or get_provider(settings)
    if isinstance(provider, NullProvider) or not provider.available:
        return 0
    batch = audit_candidates(session, limit=limit)
    if not batch:
        return 0
    profile, _version = resolve_profile(session, settings=settings)
    prompt = build_batch_analysis_prompt(
        profile, [_job_prompt_input(job, name) for job, name in batch]
    )
    try:
        raw = provider.analyze(prompt)
        parsed = parse_strict_json_array(raw.text) if raw.available else []
    except (AIProviderError, ValueError) as exc:
        logger.info("Rule audit skipped this run: %s", exc)
        return 0
    by_index = {item["index"]: item for item in parsed}
    now = datetime.now(UTC).isoformat()
    done = 0
    for i, (job, _name) in enumerate(batch):
        item = by_index.get(i)
        if item is None:
            continue
        job.rule_audit = {
            "ai_eligible": ai_eligibility(item),
            "explanation": str(item.get("explanation") or "")[:600],
            "rules": sorted(job.eligibility_failed_rules or []),
            "checked_at": now,
            "_prompt_version": PROMPT_VERSION,
        }
        done += 1
    session.flush()
    flagged = sum(1 for job, _ in batch if (job.rule_audit or {}).get("ai_eligible"))
    logger.info("Rule audit: %d jobs checked, %d the AI thinks are eligible.", done, flagged)
    return done
