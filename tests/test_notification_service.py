"""Tests for the FEAT-005 email notification service (FEAT-004 opt-in rules).

Email is OPT-IN and EXACT-MATCH only: by default a user receives nothing, and
once opted in the only job-level emails fired are for jobs in the user's final
eligible set (Stage-1 survivors INTERSECT the AI-eligible gate), the same set
shown on "Relevant For Me".

The SMTP sender is never used; a :class:`RecordingSender` captures the rendered
emails so no real email is sent and no network is touched. The persisted
Stage-1 verdict (``Job.eligible``) and the cached AI verdict
(``AiAnalysisCache``) are seeded directly -- nothing in these tests calls a
live provider. The temp-sqlite ``session`` fixture from ``conftest`` provides
an isolated database.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy.orm import Session

from jobtracker.db.models import (
    AiAnalysisCache,
    Company,
    Job,
    JobEvent,
    SavedJob,
    SourceHealth,
    User,
)
from jobtracker.notifications.sender import STATUS_OUTBOX, EmailMessage, OutboxEmailSender
from jobtracker.services import ai_service, auth_service, notification_service, user_service


class RecordingSender:
    """A fake EmailSender that records messages instead of sending them."""

    status = "sent"

    def __init__(self) -> None:
        self.messages: list[EmailMessage] = []

    def send(self, message: EmailMessage) -> str:
        self.messages.append(message)
        return self.status


def _now() -> datetime:
    return datetime.now(UTC)


def _company(session: Session, *, priority: str = "medium") -> Company:
    company = Company(
        id=f"co-{uuid.uuid4().hex[:8]}",
        name="Acme Robotics",
        ats="greenhouse",
        priority=priority,
        enabled=True,
        active=True,
    )
    session.add(company)
    session.flush()
    return company


def _job(
    session: Session,
    company: Company,
    *,
    classification: str = "strong_match",
    status: str = "active",
    fit_score: int = 90,
    term_season: str = "fall",
    duration_months: int = 4,
    country: str | None = "CA",
    location: str = "Toronto, ON",
    visa_sponsorship: str = "unknown",
    eligibility_status: str | None = None,
    eligible: bool | None = None,
    description_hash: str = "h1",
    emit_event: str | None = "new",
) -> Job:
    job = Job(
        job_id=str(uuid.uuid4()),
        company_id=company.id,
        source_job_id=f"src-{uuid.uuid4().hex[:8]}",
        title="Mechanical Engineering Intern",
        location=location,
        country=country,
        classification=classification,
        fit_score=fit_score,
        term_season=term_season,
        duration_months=duration_months,
        duration_label=f"{duration_months} months",
        visa_sponsorship=visa_sponsorship,
        eligibility_status=eligibility_status,
        eligible=eligible,
        description="Design mechanical systems and prototypes.",
        description_hash=description_hash,
        application_url="https://apply.example.com/job/123",
        source_url="https://boards.example.com/job/123",
        status=status,
        first_seen_at=_now(),
        last_seen_at=_now(),
    )
    session.add(job)
    session.flush()
    if emit_event is not None:
        session.add(
            JobEvent(
                job_id=job.job_id,
                company_id=company.id,
                event_type=emit_event,
                created_at=_now(),
            )
        )
        session.flush()
    return job


def _user(session: Session, email: str = "me@example.com") -> User:
    return auth_service.register(session, email, "pw-123456")


def _opt_in(session: Session, user: User) -> None:
    """Opt the user in to email (FEAT-004 default is OFF)."""
    user_service.set_notification_prefs(
        session, user.id, {"email_enabled": True, "email": user.email}
    )


def _seed_ai_verdict(
    session: Session,
    job: Job,
    *,
    user_id: int | None = None,
    recommendation: str = "apply",
) -> None:
    """Seed a cached AI verdict for a job (no live provider).

    The cache key must match what :func:`ai_service.get_cached` computes, so we
    resolve the same ``profile_version`` and reuse the job's ``description_hash``.
    """
    _, profile_version = ai_service.resolve_profile(session, user_id=user_id)
    session.add(
        AiAnalysisCache(
            job_id=job.job_id,
            description_hash=job.description_hash,
            profile_version=profile_version,
            result={"recommendation": recommendation},
            provider="mock",
            model="mock-1",
        )
    )
    session.flush()


def test_outbox_mode_no_creds(tmp_path) -> None:
    """The outbox backend works with no credentials and writes a file."""
    sender = OutboxEmailSender(tmp_path / "outbox")
    status = sender.send(
        EmailMessage(
            to_email="me@example.com",
            subject="Hello",
            text_body="A new job appeared.",
            html_body="<p>A new job appeared.</p>",
        )
    )
    assert status == STATUS_OUTBOX
    files = list((tmp_path / "outbox").glob("*.eml"))
    assert len(files) == 1
    assert "A new job appeared." in files[0].read_text(encoding="utf-8")


def test_no_email_by_default_even_for_strong_match(session: Session) -> None:
    """With no prefs set, a user receives NO email even for a new strong match."""
    _user(session)
    company = _company(session)
    job = _job(session, company, classification="strong_match")
    sender = RecordingSender()

    result = notification_service.run(
        session, new_job_ids=[job.job_id], sender=sender
    )

    assert result.sent == 0
    assert sender.messages == []


def test_opted_in_user_emailed_for_eligible_job(session: Session) -> None:
    """After opting in, an eligible strong match produces one email."""
    user = _user(session)
    _opt_in(session, user)
    company = _company(session)
    job = _job(session, company, classification="strong_match")
    sender = RecordingSender()

    result = notification_service.run(
        session, new_job_ids=[job.job_id], sender=sender
    )

    assert result.sent == 1
    assert len(sender.messages) == 1
    assert notification_service.RULE_STRONG_MATCH in result.rules


def test_opted_in_subscription_match_attributed(session: Session) -> None:
    """An eligible job matching a saved filter is attributed to that filter."""
    user = _user(session)
    _opt_in(session, user)
    company = _company(session)
    job = _job(session, company, classification="strong_match")
    user_service.create_saved_filter(
        session,
        user.id,
        "Strong matches",
        {"classifications": ["strong_match"]},
        notify=True,
    )
    sender = RecordingSender()

    result = notification_service.run(
        session, new_job_ids=[job.job_id], sender=sender
    )

    assert result.sent == 1
    assert notification_service.RULE_SUBSCRIPTION in result.rules
    assert any("Strong matches" in m.subject for m in sender.messages)


def test_not_emailed_for_non_eligible_job_even_if_strong_match(session: Session) -> None:
    """A Stage-1-dropped (eligible False) strong match is never emailed."""
    user = _user(session)
    _opt_in(session, user)
    company = _company(session)
    # Strong match, but the deterministic Stage-1 verdict dropped it.
    job = _job(session, company, classification="strong_match", eligible=False)
    user_service.create_saved_filter(
        session,
        user.id,
        "Strong matches",
        {"classifications": ["strong_match"]},
        notify=True,
    )
    sender = RecordingSender()

    result = notification_service.run(
        session, new_job_ids=[job.job_id], sender=sender
    )

    assert result.sent == 0
    assert sender.messages == []


def test_not_emailed_when_ai_gate_marks_ineligible(session: Session) -> None:
    """A Stage-1 survivor the AI verdict rejects (skip) is never emailed."""
    user = _user(session)
    _opt_in(session, user)
    company = _company(session)
    job = _job(session, company, classification="strong_match")
    # Cached AI verdict says skip -> dropped from the eligible set.
    _seed_ai_verdict(session, job, user_id=user.id, recommendation="skip")
    sender = RecordingSender()

    result = notification_service.run(
        session, new_job_ids=[job.job_id], sender=sender
    )

    assert result.sent == 0
    assert sender.messages == []


def test_emailed_when_ai_gate_marks_eligible(session: Session) -> None:
    """A survivor with an AI-eligible (apply) cached verdict is emailed."""
    user = _user(session)
    _opt_in(session, user)
    company = _company(session)
    job = _job(session, company, classification="strong_match")
    _seed_ai_verdict(session, job, user_id=user.id, recommendation="apply")
    sender = RecordingSender()

    result = notification_service.run(
        session, new_job_ids=[job.job_id], sender=sender
    )

    assert result.sent == 1


def test_dedup_never_sends_same_job_twice(session: Session) -> None:
    user = _user(session)
    _opt_in(session, user)
    company = _company(session)
    job = _job(session, company, classification="strong_match")
    sender = RecordingSender()

    first = notification_service.run(session, new_job_ids=[job.job_id], sender=sender)
    assert first.sent == 1

    # A second pass over the same job must send nothing new.
    second = notification_service.run(session, new_job_ids=[job.job_id], sender=sender)
    assert second.sent == 0
    assert second.skipped_duplicate == 1
    assert len(sender.messages) == 1


def test_outbox_fallback_records_when_no_smtp(session: Session, tmp_path) -> None:
    """With no SMTP configured the real sender falls back to the outbox."""
    from jobtracker.settings import Settings

    user = _user(session)
    _opt_in(session, user)
    company = _company(session)
    job = _job(session, company, classification="strong_match")
    settings = Settings(notify_outbox_dir=str(tmp_path / "outbox"))

    result = notification_service.run(
        session, new_job_ids=[job.job_id], settings=settings
    )

    assert result.sent == 1
    files = list((tmp_path / "outbox").glob("*.eml"))
    assert len(files) == 1


def test_email_body_has_all_required_fields(session: Session) -> None:
    user = _user(session)
    _opt_in(session, user)
    company = _company(session)
    job = _job(
        session,
        company,
        classification="strong_match",
        location="Austin, TX",
        term_season="spring",
        duration_months=4,
    )
    user_service.create_saved_filter(
        session, user.id, "All", {"classifications": ["strong_match"]}, notify=True
    )
    sender = RecordingSender()

    notification_service.run(session, new_job_ids=[job.job_id], sender=sender)

    assert sender.messages
    body = sender.messages[0].text_body
    html = sender.messages[0].html_body or ""
    # SPEC-required fields: application URL, company, title, location, term,
    # duration, fit score, and reason.
    assert "https://apply.example.com/job/123" in body
    assert "Acme Robotics" in body
    assert "Mechanical Engineering Intern" in body
    assert "Austin, TX" in body
    assert "spring" in body
    assert "4 months" in body
    assert "90" in body
    assert "Why:" in body
    assert "https://apply.example.com/job/123" in html


def test_removed_after_saved_notification(session: Session) -> None:
    user = _user(session)
    _opt_in(session, user)
    company = _company(session)
    job = _job(session, company, status="removed", emit_event="removed")
    session.add(SavedJob(user_id=user.id, job_id=job.job_id, created_at=_now()))
    session.flush()
    sender = RecordingSender()

    result = notification_service.run(
        session, removed_job_ids=[job.job_id], sender=sender
    )

    assert notification_service.RULE_REMOVED_AFTER_SAVED in result.rules
    assert any("removed" in m.subject.lower() for m in sender.messages)


def test_removed_after_saved_requires_opt_in(session: Session) -> None:
    """removed_after_saved is still gated by the opt-in _email_enabled."""
    user = _user(session)
    company = _company(session)
    job = _job(session, company, status="removed", emit_event="removed")
    session.add(SavedJob(user_id=user.id, job_id=job.job_id, created_at=_now()))
    session.flush()
    sender = RecordingSender()

    result = notification_service.run(
        session, removed_job_ids=[job.job_id], sender=sender
    )

    assert result.sent == 0


def test_source_failure_notification(session: Session) -> None:
    user = _user(session)  # first user is admin
    _opt_in(session, user)
    company = _company(session)
    session.add(
        SourceHealth(
            company_id=company.id,
            last_status="error",
            last_error="HTTP 500",
            last_checked_at=_now(),
        )
    )
    session.flush()
    sender = RecordingSender()

    result = notification_service.run(
        session, failed_company_ids=[company.id], sender=sender
    )

    assert notification_service.RULE_SOURCE_FAILURE in result.rules
    assert user.role == auth_service.ROLE_ADMIN


def test_itar_ineligible_never_notified(session: Session) -> None:
    from jobtracker.extract.work_auth import INELIGIBLE_ITAR

    user = _user(session)
    _opt_in(session, user)
    company = _company(session)
    job = _job(
        session,
        company,
        classification="strong_match",
        eligibility_status=INELIGIBLE_ITAR,
    )
    sender = RecordingSender()

    result = notification_service.run(
        session, new_job_ids=[job.job_id], sender=sender
    )

    assert result.sent == 0
    assert sender.messages == []


def test_run_recent_reads_events(session: Session) -> None:
    user = _user(session)
    _opt_in(session, user)
    company = _company(session)
    _job(session, company, classification="strong_match")
    sender = RecordingSender()

    result = notification_service.run_recent(session, sender=sender)

    assert result.sent >= 1
    assert any(m.to_email == user.email for m in sender.messages)


def test_digest_rolls_up_into_single_email(session: Session) -> None:
    """A user matching N eligible jobs gets ONE digest email containing N items."""
    user = _user(session)
    _opt_in(session, user)
    company = _company(session)
    jobs = [
        _job(session, company, classification="strong_match") for _ in range(3)
    ]
    user_service.create_saved_filter(
        session, user.id, "Strong", {"classifications": ["strong_match"]}, notify=True
    )
    sender = RecordingSender()

    result = notification_service.send_digest(session, sender=sender)

    # Exactly one email was sent to the user, not one per job.
    assert result.sent == 1
    assert len(sender.messages) == 1
    message = sender.messages[0]
    assert message.subject == "Your job tracker digest"
    # All three matched jobs are summarised in that one email.
    for job in jobs:
        assert job.title in message.text_body
    body = message.text_body
    assert body.count("Mechanical Engineering Intern") == 3

    # A second digest pass re-sends nothing (each job deduped per user).
    second = notification_service.send_digest(session, sender=sender)
    assert second.sent == 0
    assert len(sender.messages) == 1


def test_digest_only_sends_undelivered_jobs(session: Session) -> None:
    """After one job is digested, a new match produces a one-item roll-up."""
    user = _user(session)
    _opt_in(session, user)
    company = _company(session)
    _job(session, company, classification="strong_match")
    user_service.create_saved_filter(
        session, user.id, "Strong", {"classifications": ["strong_match"]}, notify=True
    )
    sender = RecordingSender()

    first = notification_service.send_digest(session, sender=sender)
    assert first.sent == 1

    # A second, newly detected job should yield exactly one more digest email
    # containing only the new job (the first is deduped out).
    new_job = _job(session, company, classification="strong_match")
    second = notification_service.send_digest(session, sender=sender)
    assert second.sent == 1
    assert len(sender.messages) == 2
    assert sender.messages[1].text_body.count("Mechanical Engineering Intern") == 1
    assert new_job.title in sender.messages[1].text_body


def test_default_to_email_only_used_single_user(session: Session) -> None:
    """The shared default-to address is only used in a single-user deployment."""
    from jobtracker.settings import Settings

    settings = Settings(notify_default_to_email="shared@example.com")

    # Single user with no email on the row / prefs -> default is used.
    only = User(email=None, password_hash="x", role="admin")
    session.add(only)
    session.flush()
    assert (
        notification_service._user_email(session, only, settings)
        == "shared@example.com"
    )

    # Add a second user: the default is NO LONGER used (returns None), so one
    # user's matches can never be misrouted to a shared inbox.
    second = User(email=None, password_hash="x", role="user")
    session.add(second)
    session.flush()
    assert notification_service._user_email(session, only, settings) is None


def test_disabled_email_pref_suppresses(session: Session) -> None:
    user = _user(session)
    company = _company(session)
    job = _job(session, company, classification="strong_match")
    user_service.set_notification_prefs(
        session, user.id, {"email_enabled": False, "email": user.email}
    )
    sender = RecordingSender()

    result = notification_service.run(
        session, new_job_ids=[job.job_id], sender=sender
    )

    assert result.sent == 0
