"""Email notification backends and rendering (FEAT-005).

Public surface:

* :class:`EmailMessage` -- a rendered, provider-agnostic email (subject +
  plaintext + optional HTML + recipient).
* :class:`EmailSender` -- the backend ABC.
* :class:`SmtpEmailSender` -- stdlib ``smtplib`` SSL/STARTTLS backend
  (Gmail-SMTP-compatible with an app password; works with any SMTP provider).
* :class:`OutboxEmailSender` -- the safe no-credential default that writes
  rendered emails to an outbox directory and logs a preview.
* :func:`build_sender` -- factory that picks SMTP when creds are configured,
  else the outbox. It NEVER crashes or requires credentials.
"""

from __future__ import annotations

from jobtracker.notifications.sender import (
    EmailMessage,
    EmailSender,
    OutboxEmailSender,
    SmtpEmailSender,
    build_sender,
)

__all__ = [
    "EmailMessage",
    "EmailSender",
    "OutboxEmailSender",
    "SmtpEmailSender",
    "build_sender",
]
