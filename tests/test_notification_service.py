"""Tests for the FEAT-005 email notification service.

The SMTP sender is never used; a :class:`RecordingSender` captures the rendered
emails so no real email is sent and no network is touched. The temp-sqlite
``session`` fixture from ``conftest`` provides an isolated database.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy.orm import Session

from jobtracker.db.models import Company, Job, JobEvent, SavedJob, SourceHealth, User
from jobtracker.notifications.sender import STATUS_OUTBOX, EmailMessage, OutboxEmailSender
from jobtracker.services import auth_service, notification_service, user_service


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


def test_subscription_match_queues_one_email(session: Session) -> None:
    user = _user(session)
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

    # One subscription email + one standalone strong_match rule email.
    subjects = [m.subject for m in sender.messages]
    assert any("Strong matches" in s for s in subjects)
    assert result.sent >= 1
    # The subscription rule fired exactly once for this job.
    assert notification_service.RULE_SUBSCRIPTION in result.rules


def test_non_matching_subscription_sends_nothing(session: Session) -> None:
    user = _user(session)
    company = _company(session)
    # A weak match should not match a strong-only subscription or fire rules.
    job = _job(session, company, classification="weak_match", fit_score=10)
    user_service.create_saved_filter(
        session,
        user.id,
        "Strong only",
        {"classifications": ["strong_match"]},
        notify=True,
    )
    sender = RecordingSender()

    result = notification_service.run(
        session, new_job_ids=[job.job_id], sender=sender
    )

    assert result.sent == 0
    assert sender.messages == []


def test_dedup_never_sends_same_job_rule_twice(session: Session) -> None:
    user = _user(session)
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

    first = notification_service.run(session, new_job_ids=[job.job_id], sender=sender)
    sent_first = first.sent
    assert sent_first >= 1

    # A second pass over the same job must send nothing new.
    second = notification_service.run(session, new_job_ids=[job.job_id], sender=sender)
    assert second.sent == 0
    assert second.skipped_duplicate == sent_first
    # No additional messages were delivered.
    assert len(sender.messages) == sent_first


def test_email_body_has_all_required_fields(session: Session) -> None:
    user = _user(session)
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


def test_high_priority_possible_match_rule(session: Session) -> None:
    _user(session)
    company = _company(session, priority="high")
    job = _job(session, company, classification="possible_match", fit_score=55)
    sender = RecordingSender()

    result = notification_service.run(
        session, new_job_ids=[job.job_id], sender=sender
    )

    assert notification_service.RULE_POSSIBLE_HIGH_PRIORITY in result.rules


def test_us_sponsorship_rule(session: Session) -> None:
    _user(session)
    company = _company(session)
    job = _job(
        session,
        company,
        classification="weak_match",
        country="US",
        visa_sponsorship="available",
    )
    sender = RecordingSender()

    result = notification_service.run(
        session, new_job_ids=[job.job_id], sender=sender
    )

    assert notification_service.RULE_US_SPONSORSHIP in result.rules


def test_removed_after_saved_notification(session: Session) -> None:
    user = _user(session)
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


def test_source_failure_notification(session: Session) -> None:
    user = _user(session)  # first user is admin
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

    _user(session)
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
    company = _company(session)
    _job(session, company, classification="strong_match")
    user_service.create_saved_filter(
        session, user.id, "Strong", {"classifications": ["strong_match"]}, notify=True
    )
    sender = RecordingSender()

    result = notification_service.run_recent(session, sender=sender)

    assert result.sent >= 1
    assert any(m.to_email == user.email for m in sender.messages)


def test_digest_rolls_up_into_single_email(session: Session) -> None:
    """A user matching N jobs gets ONE digest email containing N items."""
    user = _user(session)
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
