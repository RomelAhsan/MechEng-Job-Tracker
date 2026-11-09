"""Pure, testable email rendering for notifications (FEAT-005).

Renders a list of matched jobs into a plaintext + HTML email body using jinja2
templates. Kept free of DB/session access so it is trivially unit-testable; the
notification service builds the :class:`NotificationItem` list and hands it here.

Every item carries the SPEC-required fields (direct application URL, company,
title, location, term, duration, fit score, and reason), and the templates
surface all of them.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache

from jinja2 import Environment, PackageLoader, select_autoescape


@dataclass(frozen=True)
class NotificationItem:
    """One matched job rendered into an email, with all SPEC-required fields."""

    job_id: str
    company_name: str
    title: str
    location: str
    term_season: str
    duration: str
    fit_score: int | None
    application_url: str | None
    reason: str


@dataclass(frozen=True)
class RenderedEmail:
    """A rendered email body pair (plaintext + HTML)."""

    text_body: str
    html_body: str


@lru_cache(maxsize=1)
def _env() -> Environment:
    return Environment(
        loader=PackageLoader("jobtracker.notifications", "templates"),
        autoescape=select_autoescape(["html", "xml", "j2"]),
        trim_blocks=True,
        lstrip_blocks=True,
    )


def render_email(intro: str, items: list[NotificationItem]) -> RenderedEmail:
    """Render the plaintext + HTML bodies for a notification email."""
    env = _env()
    ctx = {"intro": intro, "items": items}
    text = env.get_template("notification.txt.j2").render(**ctx)
    html = env.get_template("notification.html.j2").render(**ctx)
    return RenderedEmail(text_body=text, html_body=html)
