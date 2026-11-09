"""Provider-agnostic email sender backends (FEAT-005).

Two backends implement the same :class:`EmailSender` interface:

* :class:`SmtpEmailSender` sends through any SMTP server using the stdlib
  ``smtplib`` (STARTTLS or implicit SSL). It is Gmail-SMTP-compatible: use
  ``smtp.gmail.com:587`` with an *App Password* (not the account password).
* :class:`OutboxEmailSender` requires no credentials: it appends each rendered
  email to an outbox directory (one ``.eml``-style file per message) and logs a
  short preview. This is the safe default so the app always runs without creds.

:func:`build_sender` inspects :class:`jobtracker.settings.Settings` and returns
the SMTP backend when a host + credentials are configured, otherwise the
outbox. It never raises for missing configuration.
"""

from __future__ import annotations

import logging
import smtplib
import ssl
from dataclasses import dataclass
from datetime import UTC, datetime
from email.message import EmailMessage as MimeEmailMessage
from pathlib import Path

from jobtracker.settings import Settings

logger = logging.getLogger("jobtracker.notifications")

#: Status recorded in NotificationLog by each backend.
STATUS_SENT = "sent"
STATUS_OUTBOX = "outbox"


@dataclass(frozen=True)
class EmailMessage:
    """A rendered, provider-agnostic email ready to hand to a backend."""

    to_email: str
    subject: str
    text_body: str
    html_body: str | None = None
    from_email: str | None = None


def _build_mime(message: EmailMessage, default_from: str) -> MimeEmailMessage:
    mime = MimeEmailMessage()
    mime["Subject"] = message.subject
    mime["From"] = message.from_email or default_from
    mime["To"] = message.to_email
    mime.set_content(message.text_body)
    if message.html_body:
        mime.add_alternative(message.html_body, subtype="html")
    return mime


class EmailSender:
    """Backend interface: deliver a single :class:`EmailMessage`.

    Implementations return the status string recorded in NotificationLog
    (:data:`STATUS_SENT` or :data:`STATUS_OUTBOX`).
    """

    #: ``sent`` for real delivery, ``outbox`` for the no-cred default.
    status: str = STATUS_SENT

    def send(self, message: EmailMessage) -> str:  # pragma: no cover - interface
        raise NotImplementedError


class SmtpEmailSender(EmailSender):
    """Send email through an SMTP server (STARTTLS or implicit SSL)."""

    status = STATUS_SENT

    def __init__(
        self,
        host: str,
        port: int,
        username: str | None,
        password: str | None,
        *,
        use_tls: bool = True,
        from_email: str | None = None,
        timeout: float = 30.0,
    ) -> None:
        self.host = host
        self.port = port
        self.username = username
        self.password = password
        self.use_tls = use_tls
        self.from_email = from_email or username or "jobtracker@localhost"
        self.timeout = timeout

    def send(self, message: EmailMessage) -> str:
        mime = _build_mime(message, self.from_email)
        context = ssl.create_default_context()
        if self.use_tls:
            with smtplib.SMTP(self.host, self.port, timeout=self.timeout) as client:
                client.ehlo()
                client.starttls(context=context)
                client.ehlo()
                if self.username and self.password:
                    client.login(self.username, self.password)
                client.send_message(mime)
        else:
            with smtplib.SMTP_SSL(
                self.host, self.port, timeout=self.timeout, context=context
            ) as client:
                if self.username and self.password:
                    client.login(self.username, self.password)
                client.send_message(mime)
        logger.info("Sent email to %s via SMTP (%s)", message.to_email, self.host)
        return self.status


class OutboxEmailSender(EmailSender):
    """No-credential default: write rendered emails to an outbox directory."""

    status = STATUS_OUTBOX

    def __init__(self, outbox_dir: str | Path, *, from_email: str | None = None) -> None:
        self.outbox_dir = Path(outbox_dir)
        self.from_email = from_email or "jobtracker@localhost"

    def send(self, message: EmailMessage) -> str:
        self.outbox_dir.mkdir(parents=True, exist_ok=True)
        mime = _build_mime(message, self.from_email)
        stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%f")
        safe_to = message.to_email.replace("@", "_at_").replace("/", "_")
        path = self.outbox_dir / f"{stamp}_{safe_to}.eml"
        path.write_text(mime.as_string(), encoding="utf-8")
        preview = message.text_body.strip().splitlines()
        first_line = preview[0] if preview else ""
        logger.info(
            "Outbox email -> %s | to=%s | subject=%s | %s",
            path,
            message.to_email,
            message.subject,
            first_line,
        )
        return self.status


def build_sender(settings: Settings) -> EmailSender:
    """Return the SMTP backend when creds are configured, else the outbox.

    A sender is considered "configured" when ``smtp_host`` plus a username and
    password are all present. Absent any of those, the safe outbox backend is
    returned so the notifier always runs without credentials.
    """
    if settings.smtp_host and settings.smtp_username and settings.smtp_password:
        return SmtpEmailSender(
            host=settings.smtp_host,
            port=settings.smtp_port,
            username=settings.smtp_username,
            password=settings.smtp_password,
            use_tls=settings.smtp_use_tls,
            from_email=settings.notify_from_email,
            timeout=settings.http_timeout,
        )
    return OutboxEmailSender(
        settings.notify_outbox_dir, from_email=settings.notify_from_email
    )
