"""Email notification service (FEAT-005).

Evaluates newly-detected / changed / removed jobs against each user's
subscriptions (saved filters with ``notify=True``) and per-user notification
preferences, builds the SPEC notification rules, dedups via
:class:`~jobtracker.db.models.NotificationLog`, renders the email with jinja2,
and delivers it through an :class:`~jobtracker.notifications.EmailSender`
(SMTP when configured, else the safe no-credential outbox).

All business logic lives here; the CLI, scheduler, and Streamlit layers are
thin callers. Nothing in this module imports Streamlit or raises on missing
SMTP credentials.

Email is OPT-IN and EXACT-MATCH only (FEAT-004): no account receives email
until it sets ``email_enabled=True``, and the only job-level emails fired are
for jobs in the user's FINAL ELIGIBLE SET -- the same set shown on "Relevant
For Me" (Stage-1 survivors INTERSECT the AI-eligible gate). The old broad SPEC
rules no longer trigger emails on their own; a subscription (saved filter) is
intersected with the eligible set too, so it can never surface a non-eligible /
non-co-op job.

Active rules (one ``rule`` key per fired rule, used as part of the dedup key so
the same job never re-notifies for the same rule):

* ``strong_match``            -- a new job in the user's exact-match eligible set
                                 (not attributable to a specific subscription).
* ``subscription``            -- an eligible new job also matching a saved filter.
* ``source_failure``          -- a tracked source failed its fetch.
* ``removed_after_saved``     -- a job the user saved was removed from its board.

Retained-but-disabled rule keys (``possible_high_priority``,
``spring_four_month``, ``preferred_location``, ``us_sponsorship``) are kept only
for NotificationLog history / back-compat and no longer fire automatically.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from jobtracker.db.models import (
    Company,
    NotificationLog,
    SavedJob,
    SourceHealth,
    User,
)
from jobtracker.notifications import EmailMessage, build_sender
from jobtracker.notifications.renderer import NotificationItem, render_email
from jobtracker.notifications.sender import EmailSender
from jobtracker.services import dashboard_service, user_service
from jobtracker.services.dashboard_service import JobFilters, JobView
from jobtracker.settings import Settings, get_settings

logger = logging.getLogger("jobtracker.services.notification")

DEFAULT_CHANNEL = "email"

# Rule keys (also the dedup key component).
RULE_STRONG_MATCH = "strong_match"
RULE_POSSIBLE_HIGH_PRIORITY = "possible_high_priority"
RULE_SPRING_FOUR_MONTH = "spring_four_month"
RULE_PREFERRED_LOCATION = "preferred_location"
RULE_US_SPONSORSHIP = "us_sponsorship"
RULE_SUBSCRIPTION = "subscription"
RULE_SOURCE_FAILURE = "source_failure"
RULE_REMOVED_AFTER_SAVED = "removed_after_saved"
RULE_DIGEST = "digest"

def _now() -> datetime:
    return datetime.now(UTC)


@dataclass
class PendingNotification:
    """One email about to be considered for delivery (pre-dedup)."""

    user_id: int
    to_email: str
    rule: str
    channel: str
    subject: str
    intro: str
    items: list[NotificationItem]
    #: ``job_id`` for job-level rules; ``company_id`` for source-level rules.
    job_id: str | None = None
    company_id: str | None = None
    #: For a multi-job digest roll-up: every job id summarised in this one
    #: email. Each is logged separately so the same job is never re-digested,
    #: while the user still receives a single email. Empty for single-job
    #: pendings (dedup/record then use ``job_id``).
    digest_job_ids: list[str] = field(default_factory=list)


@dataclass
class NotifyResult:
    """Outcome of a :func:`run` pass."""

    sent: int = 0
    skipped_duplicate: int = 0
    rules: list[str] = field(default_factory=list)


# --- Preferences / recipient resolution -----------------------------------


def _user_email(session: Session, user: User, settings: Settings) -> str | None:
    """Resolve the destination email for a user (prefs > user row > default).

    The ``notify_default_to_email`` fallback is ONLY used in a single-user
    deployment. In a genuine multi-user setup falling back to a shared default
    address could misroute one user's matches to another inbox, so when more
    than one user exists we return ``None`` (no address) rather than the shared
    default and that user simply gets no mail until they set their own address.
    """
    prefs = user_service.get_notification_prefs(session, user.id)
    if prefs.get("email"):
        return str(prefs["email"])
    if user.email:
        return user.email
    if _is_single_user(session):
        return settings.notify_default_to_email
    return None


def _is_single_user(session: Session) -> bool:
    """True when the deployment has at most one user account."""
    count = session.scalar(select(func.count()).select_from(User)) or 0
    return count <= 1


def _email_enabled(session: Session, user_id: int) -> bool:
    """Whether the user opted into email notifications (default: DISABLED).

    Email is OPT-IN (FEAT-004): absent explicit prefs we treat email as
    DISABLED so no account ever receives mail until it deliberately opts in by
    setting ``email_enabled`` to ``True`` on the Notifications page. Only an
    explicit ``prefs['email_enabled'] == True`` enables delivery.
    """
    prefs = user_service.get_notification_prefs(session, user_id)
    if "email_enabled" not in prefs:
        return False
    return bool(prefs["email_enabled"])


# --- Item building ---------------------------------------------------------


def _duration_label(view: JobView) -> str:
    if view.duration_label:
        return view.duration_label
    if view.duration_months:
        return f"{view.duration_months} months"
    return "unknown"


def _item(view: JobView, reason: str) -> NotificationItem:
    return NotificationItem(
        job_id=view.job_id,
        company_name=view.company_name,
        title=view.title,
        location=view.location,
        term_season=view.term_season,
        duration=_duration_label(view),
        fit_score=view.fit_score,
        application_url=view.application_url or view.source_url,
        reason=reason,
    )


# --- Rule evaluation -------------------------------------------------------


# NOTE (FEAT-004): the broad standalone SPEC rules (possible_high_priority,
# spring_four_month, preferred_location, us_sponsorship) are intentionally NOT
# evaluated as automatic email triggers anymore. Email now fires ONLY on the
# user's exact-match "Relevant For Me" set (Stage-1 survivors INTERSECT
# AI-eligible); see ``_build_subscription_and_rule_pendings``. The rule-key
# constants are retained for NotificationLog history/back-compat.


# --- Dedup -----------------------------------------------------------------


def _already_sent(
    session: Session,
    user_id: int,
    job_id: str | None,
    channel: str,
    rule: str,
    *,
    company_id: str | None = None,
) -> bool:
    stmt = select(NotificationLog.id).where(
        NotificationLog.user_id == user_id,
        NotificationLog.channel == channel,
        NotificationLog.rule == rule,
    )
    if job_id is not None:
        stmt = stmt.where(NotificationLog.job_id == job_id)
    else:
        stmt = stmt.where(NotificationLog.job_id.is_(None))
    if company_id is not None:
        stmt = stmt.where(NotificationLog.company_id == company_id)
    return session.scalars(stmt).first() is not None


def _record(
    session: Session,
    pending: PendingNotification,
    status: str,
) -> None:
    # A digest rolls up many jobs into one email; log each summarised job so
    # the same job is never re-digested to this user (dedup key is per job).
    job_ids: list[str | None] = (
        list(pending.digest_job_ids) if pending.digest_job_ids else [pending.job_id]
    )
    now = _now()
    for job_id in job_ids:
        session.add(
            NotificationLog(
                user_id=pending.user_id,
                job_id=job_id,
                company_id=pending.company_id,
                channel=pending.channel,
                rule=pending.rule,
                subject=pending.subject,
                to_email=pending.to_email,
                sent_at=now,
                status=status,
            )
        )


# --- Pending builders ------------------------------------------------------


def _user_eligible_job_ids(session: Session, user_id: int) -> set[str]:
    """Return the user's FINAL ELIGIBLE SET (the 'Relevant For Me' set).

    This is exactly what :func:`dashboard_service.list_relevant_jobs` returns
    for the user: the Stage-1 deterministic survivors INTERSECT the Stage-2 AI
    eligibility gate (reading the cached AI verdict with the never-empty
    fallback). Routing email through this guarantees the emailed set never
    diverges from the page, so a non-eligible / non-co-op job is never emailed
    even if it is a strong_match.
    """
    relevant = dashboard_service.list_relevant_jobs(session, user_id=user_id)
    return {view.job_id for view in relevant}


def _build_subscription_and_rule_pendings(
    session: Session,
    *,
    job_ids: list[str],
    settings: Settings,
) -> list[PendingNotification]:
    """Build EXACT-MATCH-ONLY job-level pending notifications for all users.

    FEAT-004: the ONLY job-level emails fired are for jobs in the user's FINAL
    ELIGIBLE SET -- the same set returned by
    :func:`dashboard_service.list_relevant_jobs` (Stage-1 survivors INTERSECT
    the AI-eligible gate). Concretely, among the newly-detected ``job_ids`` we
    email only those also in the user's eligible set (and then deduped by
    :class:`NotificationLog`).

    The old broad SPEC rules (strong_match, possible_high_priority,
    spring_four_month, preferred_location, us_sponsorship) no longer trigger
    emails on their own -- a job may only be emailed when it is in the eligible
    set. The subscription (saved-filter) path is kept but INTERSECTED with the
    eligible set too, so a subscription can never surface a non-eligible /
    non-co-op job.
    """
    new_ids = set(job_ids)
    if not new_ids:
        return []
    views = dashboard_service.views_for_job_ids(session, job_ids)
    if not views:
        return []
    pendings: list[PendingNotification] = []

    for user in session.scalars(select(User)):
        if not _email_enabled(session, user.id):
            continue
        to_email = _user_email(session, user, settings)
        if not to_email:
            continue

        # The user's exact-match "Relevant For Me" set, intersected with the
        # just-detected jobs: this is the ONLY set that may be emailed.
        eligible_ids = _user_eligible_job_ids(session, user.id) & new_ids
        if not eligible_ids:
            continue

        # Which of the eligible new jobs matched one of the user's notify
        # subscriptions (so we can attribute the email to that subscription).
        subs = [s for s in user_service.list_saved_filters(session, user.id) if s.notify]
        sub_name_by_job: dict[str, str] = {}
        for sub in subs:
            jf: JobFilters = dashboard_service.filters_from_dict(sub.filter_json)
            for job_id in eligible_ids:
                if job_id in sub_name_by_job:
                    continue
                view = views.get(job_id)
                if view is None or view.status != "active":
                    continue
                if dashboard_service.matches_filters(view, jf):
                    sub_name_by_job[job_id] = sub.name

        for job_id in eligible_ids:
            view = views.get(job_id)
            if view is None or view.status != "active":
                continue
            sub_name = sub_name_by_job.get(job_id)
            if sub_name is not None:
                reason = f"Matches your subscription '{sub_name}'"
                pendings.append(
                    PendingNotification(
                        user_id=user.id,
                        to_email=to_email,
                        rule=RULE_SUBSCRIPTION,
                        channel=DEFAULT_CHANNEL,
                        subject=f"New job matching '{sub_name}': {view.title}",
                        intro=f"A new posting matches your subscription '{sub_name}':",
                        items=[_item(view, reason)],
                        job_id=view.job_id,
                    )
                )
            else:
                reason = "New job matching all your criteria (Relevant For Me)"
                pendings.append(
                    PendingNotification(
                        user_id=user.id,
                        to_email=to_email,
                        rule=RULE_STRONG_MATCH,
                        channel=DEFAULT_CHANNEL,
                        subject=f"New relevant job: {view.title}",
                        intro=f"{reason}:",
                        items=[_item(view, reason)],
                        job_id=view.job_id,
                    )
                )
    return pendings


def _build_removed_after_saved_pendings(
    session: Session,
    *,
    removed_job_ids: list[str],
    settings: Settings,
) -> list[PendingNotification]:
    """Notify users whose saved jobs were removed from their board."""
    if not removed_job_ids:
        return []
    views = dashboard_service.views_for_job_ids(session, removed_job_ids)
    pendings: list[PendingNotification] = []
    for job_id in removed_job_ids:
        view = views.get(job_id)
        if view is None:
            continue
        savers = session.scalars(
            select(SavedJob.user_id).where(SavedJob.job_id == job_id)
        ).all()
        for user_id in savers:
            if user_id is None or not _email_enabled(session, user_id):
                continue
            user = session.get(User, user_id)
            if user is None:
                continue
            to_email = _user_email(session, user, settings)
            if not to_email:
                continue
            reason = "A job you saved was removed from its board"
            pendings.append(
                PendingNotification(
                    user_id=user_id,
                    to_email=to_email,
                    rule=RULE_REMOVED_AFTER_SAVED,
                    channel=DEFAULT_CHANNEL,
                    subject=f"Saved job removed: {view.title}",
                    intro=f"{reason}:",
                    items=[_item(view, reason)],
                    job_id=job_id,
                )
            )
    return pendings


def _build_source_failure_pendings(
    session: Session,
    *,
    company_ids: list[str],
    settings: Settings,
) -> list[PendingNotification]:
    """Notify admins when a tracked source failed its last fetch."""
    if not company_ids:
        return []
    admins = session.scalars(select(User).where(User.role == "admin")).all()
    if not admins:
        admins = session.scalars(select(User)).all()
    pendings: list[PendingNotification] = []
    for company_id in company_ids:
        health = session.get(SourceHealth, company_id)
        if health is None or health.last_status != "error":
            continue
        company = session.get(Company, company_id)
        name = company.name if company else company_id
        error = health.last_error or "unknown error"
        for user in admins:
            if not _email_enabled(session, user.id):
                continue
            to_email = _user_email(session, user, settings)
            if not to_email:
                continue
            reason = f"Source '{name}' failed its last fetch: {error}"
            item = NotificationItem(
                job_id=f"source:{company_id}",
                company_name=name,
                title="Source fetch failure",
                location="",
                term_season="n/a",
                duration="n/a",
                fit_score=None,
                application_url=company.careers_url if company else None,
                reason=reason,
            )
            pendings.append(
                PendingNotification(
                    user_id=user.id,
                    to_email=to_email,
                    rule=RULE_SOURCE_FAILURE,
                    channel=DEFAULT_CHANNEL,
                    subject=f"Source failure: {name}",
                    intro=reason,
                    items=[item],
                    company_id=company_id,
                )
            )
    return pendings


# --- Delivery --------------------------------------------------------------


def _deliver(
    session: Session,
    pendings: list[PendingNotification],
    sender: EmailSender,
) -> NotifyResult:
    result = NotifyResult()
    for pending in pendings:
        # Digests dedup per summarised job in ``send_digest`` before building
        # the single roll-up pending, so the pending-level check (which is
        # job-level) is skipped for them; every other pending is job/company-
        # scoped and deduped here.
        if not pending.digest_job_ids and _already_sent(
            session,
            pending.user_id,
            pending.job_id,
            pending.channel,
            pending.rule,
            company_id=pending.company_id,
        ):
            result.skipped_duplicate += 1
            continue
        rendered = render_email(pending.intro, pending.items)
        message = EmailMessage(
            to_email=pending.to_email,
            subject=pending.subject,
            text_body=rendered.text_body,
            html_body=rendered.html_body,
        )
        try:
            status = sender.send(message)
        except Exception:  # noqa: BLE001 - delivery never crashes the caller.
            logger.exception(
                "Failed to send notification (rule=%s user=%s)",
                pending.rule,
                pending.user_id,
            )
            continue
        _record(session, pending, status)
        result.sent += 1
        result.rules.append(pending.rule)
    session.flush()
    return result


# --- Public entry points ---------------------------------------------------


def run(
    session: Session,
    *,
    new_job_ids: list[str] | None = None,
    removed_job_ids: list[str] | None = None,
    failed_company_ids: list[str] | None = None,
    settings: Settings | None = None,
    sender: EmailSender | None = None,
) -> NotifyResult:
    """Evaluate the given changes and send any due, non-duplicate emails.

    Safe to call with no SMTP creds (falls back to the outbox) and with empty
    inputs (no-op). Called by ``jobtracker notify`` and, when enabled, by the
    scheduler after each poll.
    """
    settings = settings or get_settings()
    sender = sender or build_sender(settings)

    pendings: list[PendingNotification] = []
    pendings += _build_subscription_and_rule_pendings(
        session, job_ids=list(new_job_ids or []), settings=settings
    )
    pendings += _build_removed_after_saved_pendings(
        session, removed_job_ids=list(removed_job_ids or []), settings=settings
    )
    pendings += _build_source_failure_pendings(
        session, company_ids=list(failed_company_ids or []), settings=settings
    )
    return _deliver(session, pendings, sender)


def _recent_event_job_ids(
    session: Session,
    event_types: tuple[str, ...],
    *,
    since: datetime | None,
    company_ids: list[str] | None = None,
) -> list[str]:
    from jobtracker.db.models import JobEvent

    stmt = select(JobEvent.job_id).where(JobEvent.event_type.in_(event_types))
    if since is not None:
        stmt = stmt.where(JobEvent.created_at >= since)
    if company_ids is not None:
        stmt = stmt.where(JobEvent.company_id.in_(company_ids))
    seen: list[str] = []
    for (job_id,) in session.execute(stmt):
        if job_id is not None and job_id not in seen:
            seen.append(job_id)
    return seen


def _failed_company_ids(
    session: Session, *, company_ids: list[str] | None = None
) -> list[str]:
    stmt = select(SourceHealth.company_id).where(SourceHealth.last_status == "error")
    if company_ids is not None:
        stmt = stmt.where(SourceHealth.company_id.in_(company_ids))
    return [
        company_id
        for (company_id,) in session.execute(stmt)
        if company_id is not None
    ]


def run_recent(
    session: Session,
    *,
    since: datetime | None = None,
    company_ids: list[str] | None = None,
    settings: Settings | None = None,
    sender: EmailSender | None = None,
) -> NotifyResult:
    """Evaluate recently new/changed/removed jobs + failed sources and notify.

    Reads the detection audit log (:class:`JobEvent`) and source health to find
    what changed since ``since`` (or across all history when ``None``), then
    delegates to :func:`run`. When ``company_ids`` is given the scan is scoped
    to those companies so post-poll notifications stay proportional to the
    just-polled source (O(window) rather than O(all history)); dedup via
    NotificationLog still guarantees nothing is re-sent even when a window
    overlaps previous runs. Backs ``python -m jobtracker notify``.
    """
    new_or_changed = _recent_event_job_ids(
        session, ("new", "changed", "reactivated"), since=since, company_ids=company_ids
    )
    removed = _recent_event_job_ids(
        session, ("removed",), since=since, company_ids=company_ids
    )
    failed = _failed_company_ids(session, company_ids=company_ids)
    return run(
        session,
        new_job_ids=new_or_changed,
        removed_job_ids=removed,
        failed_company_ids=failed,
        settings=settings,
        sender=sender,
    )


def send_digest(
    session: Session,
    *,
    since: datetime | None = None,
    settings: Settings | None = None,
    sender: EmailSender | None = None,
) -> NotifyResult:
    """Send each subscribed user a single summary email of recent matches.

    A digest is a periodic roll-up (e.g. daily) rather than per-job alerts: for
    every user with email enabled it collects the active strong/possible
    matches that match any of their ``notify`` subscriptions since ``since`` and
    sends one email. Deduped per rule ``digest`` + job so a job is summarised to
    a user at most once. Safe with no creds (outbox).
    """
    settings = settings or get_settings()
    sender = sender or build_sender(settings)

    job_ids = _recent_event_job_ids(
        session, ("new", "changed", "reactivated"), since=since
    )
    views = dashboard_service.views_for_job_ids(session, job_ids)
    pendings: list[PendingNotification] = []

    for user in session.scalars(select(User)):
        if not _email_enabled(session, user.id):
            continue
        to_email = _user_email(session, user, settings)
        if not to_email:
            continue
        subs = [s for s in user_service.list_saved_filters(session, user.id) if s.notify]
        if not subs:
            continue
        # Intersect subscription matches with the user's exact-match eligible
        # set so a digest can never surface a non-eligible / non-co-op job.
        eligible_ids = _user_eligible_job_ids(session, user.id)
        matched: dict[str, NotificationItem] = {}
        for sub in subs:
            jf = dashboard_service.filters_from_dict(sub.filter_json)
            for view in views.values():
                if view.status != "active":
                    continue
                if view.job_id in matched or view.job_id not in eligible_ids:
                    continue
                if dashboard_service.matches_filters(view, jf):
                    matched[view.job_id] = _item(
                        view, f"Matches your subscription '{sub.name}'"
                    )

        # Dedup per (user, job) so a job is summarised to a user at most once,
        # but roll the still-undelivered matches into a SINGLE digest email
        # (one pending with items=[...]) rather than one email per job.
        fresh = {
            job_id: item
            for job_id, item in matched.items()
            if not _already_sent(
                session, user.id, job_id, DEFAULT_CHANNEL, RULE_DIGEST
            )
        }
        if not fresh:
            continue
        pendings.append(
            PendingNotification(
                user_id=user.id,
                to_email=to_email,
                rule=RULE_DIGEST,
                channel=DEFAULT_CHANNEL,
                subject="Your job tracker digest",
                intro="Here are recent jobs matching your subscriptions:",
                items=list(fresh.values()),
                digest_job_ids=list(fresh.keys()),
            )
        )
    return _deliver(session, pendings, sender)


def notify_history(session: Session, user_id: int, *, limit: int = 100) -> list[NotificationLog]:
    """Return a user's notification history, newest first (for the UI)."""
    return list(
        session.scalars(
            select(NotificationLog)
            .where(NotificationLog.user_id == user_id)
            .order_by(NotificationLog.id.desc())
            .limit(limit)
        ).all()
    )
