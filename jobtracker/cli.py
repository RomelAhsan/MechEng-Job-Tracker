"""Typer CLI exposing the full SPEC command surface.

Every SPEC command is implemented: company management (including
add-company-by-name), ingestion + screening, the AI ``analyze`` engine, email
``notify``/``digest``, the background ``scheduler``, ``backup``/``restore``, and
the Streamlit ``dashboard``. ``init-db`` runs Alembic migrations
programmatically.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import typer
from sqlalchemy.orm import Session

from jobtracker.db.base import create_session_factory
from jobtracker.logging_setup import configure
from jobtracker.settings import get_settings

app = typer.Typer(help="Waterloo Mechanical Engineering Co-op Job Tracker")


@contextmanager
def _session_scope() -> Iterator[Session]:
    """Yield a DB session, committing on success and rolling back on error."""
    factory = create_session_factory()
    session = factory()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


@app.callback()
def main() -> None:
    """Configure logging for every CLI invocation."""
    configure(get_settings())


@app.command("init-db")
def init_db() -> None:
    """Create or upgrade the database to the latest Alembic revision."""
    from alembic import command
    from alembic.config import Config

    root = Path(__file__).resolve().parent.parent
    cfg = Config(str(root / "alembic.ini"))
    cfg.set_main_option("script_location", str(root / "migrations"))
    command.upgrade(cfg, "head")
    typer.echo("Database is at Alembic head.")


@app.command("create-user")
def create_user(
    email: str = typer.Option(
        ...,
        "--email",
        help="Account email; notifications are sent here.",
        envvar="JOBTRACKER_USER_EMAIL",
    ),
    password: str = typer.Option(
        ...,
        "--password",
        help="Account password (read from JOBTRACKER_USER_PASSWORD if unset).",
        envvar="JOBTRACKER_USER_PASSWORD",
    ),
    display_name: str | None = typer.Option(
        None, "--display-name", help="Optional display name."
    ),
) -> None:
    """Create a user account if one does not already exist (idempotent).

    Unattended setups (scheduled CI runs, hosted deployments) have nobody to
    fill in the dashboard registration form, yet notifications are per-user --
    with no account the notifier has no recipient and silently sends nothing.
    This command bootstraps that account and is a no-op when the email already
    exists, so it is safe to run on every scheduled poll.
    """
    from jobtracker.services import auth_service

    normalized = (email or "").strip().lower()
    if not normalized:
        raise typer.BadParameter("--email must not be empty.")
    if not password:
        raise typer.BadParameter("--password must not be empty.")

    with _session_scope() as session:
        if auth_service.get_by_email(session, normalized) is not None:
            typer.echo(f"User already exists: {normalized} (no changes made).")
            return

        try:
            from jobtracker.config.user_loader import load_user_profile

            seed = load_user_profile(get_settings().user_file).model_dump()
        except Exception:  # noqa: BLE001 - seeding is best-effort, never blocks.
            seed = {}

        user = auth_service.register(
            session,
            normalized,
            password,
            display_name=display_name or None,
            seed_profile=seed,
        )
        typer.echo(f"Created user {normalized} (role={user.role}).")


@app.command("add-company")
def add_company(
    url: str = typer.Argument(..., help="Careers URL to detect and add"),
    company_id: str | None = typer.Option(None, "--id", help="Explicit company id/slug"),
    name: str | None = typer.Option(None, "--name", help="Display name"),
    priority: str = typer.Option("medium", "--priority", help="high|medium|low"),
) -> None:
    """Add a company by detecting its ATS from a careers URL."""
    from jobtracker.connectors.http import HttpClient
    from jobtracker.services import company_service

    with _session_scope() as session, HttpClient() as http:
        result = company_service.add_from_url(
            session, url, http, company_id=company_id, name=name, priority=priority
        )
    typer.echo(
        f"Added {result.company_id} (ats={result.ats}, token={result.board_token}); "
        f"baseline imported {result.baseline_jobs} jobs."
    )


@app.command("add-company-by-name")
def add_company_by_name(
    name: str = typer.Argument(..., help="Company name to resolve, e.g. 'Toyota'"),
    company_id: str | None = typer.Option(None, "--id", help="Explicit company id/slug"),
    priority: str = typer.Option("medium", "--priority", help="high|medium|low"),
    confirm: bool = typer.Option(
        False,
        "--confirm",
        help="Import a found board even if its identity could not be confirmed.",
    ),
) -> None:
    """Add a company by NAME: probe public boards and baseline-import the match.

    Resolves the name to a public Greenhouse/Lever/Ashby/Workable board, picks
    the first that returns real postings whose identity matches the name, and
    baseline-imports it. If a board is found but its identity cannot be
    confirmed (a possible slug collision), it is NOT imported; re-run with
    ``--confirm`` to import it anyway. If nothing resolves it prints a helpful
    message (use ``add-company`` with the careers URL instead).
    """
    from jobtracker.connectors.http import HttpClient
    from jobtracker.services import company_service

    with _session_scope() as session, HttpClient() as http:
        result = company_service.add_from_name(
            session, name, http, company_id=company_id, priority=priority,
            confirm=confirm,
        )
    typer.echo(result.message)
    if result.needs_verification:
        typer.echo("Re-run with --confirm to import this board.")
    if not result.resolved:
        raise typer.Exit(code=1)


def _sync_seed(session: Session) -> None:
    """Seed the DB from companies.yaml (upsert by id) before read/run commands."""
    from jobtracker.config.companies_loader import load_companies
    from jobtracker.services import company_service

    settings = get_settings()
    companies = load_companies(settings.companies_file)
    company_service.sync_from_yaml(session, companies)


@app.command("list-companies")
def list_companies() -> None:
    """List tracked companies."""
    from jobtracker.services import company_service

    with _session_scope() as session:
        _sync_seed(session)
        companies = company_service.list_companies(session)
        if not companies:
            typer.echo("No companies tracked yet.")
            return
        for company in companies:
            flags = []
            if not company.enabled:
                flags.append("disabled")
            if not company.active:
                flags.append("inactive")
            suffix = f" [{', '.join(flags)}]" if flags else ""
            typer.echo(
                f"{company.id}\t{company.ats}\t{company.priority or 'medium'}\t"
                f"{company.name}{suffix}"
            )


@app.command("enable-company")
def enable_company(company_id: str = typer.Argument(...)) -> None:
    """Enable a company for ingestion."""
    from jobtracker.services import company_service

    with _session_scope() as session:
        company_service.enable(session, company_id)
    typer.echo(f"Enabled {company_id}.")


@app.command("disable-company")
def disable_company(company_id: str = typer.Argument(...)) -> None:
    """Disable a company (kept in the database)."""
    from jobtracker.services import company_service

    with _session_scope() as session:
        company_service.disable(session, company_id)
    typer.echo(f"Disabled {company_id}.")


@app.command("remove-company")
def remove_company(company_id: str = typer.Argument(...)) -> None:
    """Remove a company (marks inactive; history kept)."""
    from jobtracker.services import company_service

    with _session_scope() as session:
        company_service.remove(session, company_id)
    typer.echo(f"Removed {company_id} (history retained).")


@app.command("run")
def run(company: str | None = typer.Option(None, "--company", help="Limit to one company")) -> None:
    """Run an ingestion pass over enabled companies."""
    from jobtracker.connectors.http import HttpClient
    from jobtracker.services import ingest_service

    company_ids = [company] if company else None
    factory = create_session_factory()
    session = factory()
    try:
        _sync_seed(session)
        session.commit()
        with HttpClient() as http:
            summary = ingest_service.run(session, http, company_ids=company_ids)
    finally:
        session.close()
    typer.echo(
        f"Run complete: {len(summary.companies)} companies, "
        f"{summary.total_new} new, {summary.total_changed} changed, "
        f"{summary.errors} errors."
    )


@app.command("screen")
def screen() -> None:
    """Classify, extract, and score stored jobs."""
    from jobtracker.services import screening_service

    with _session_scope() as session:
        summary = screening_service.screen_jobs(session)
    typer.echo(
        f"Screened {summary.screened} jobs: {summary.strong} strong, "
        f"{summary.possible} possible, {summary.weak} weak, "
        f"{summary.not_relevant} not relevant."
    )


@app.command("export")
def export(
    fmt: str = typer.Option("markdown", "--format", help="markdown|json|csv"),
    output: str | None = typer.Option(None, "--output", help="Output path"),
) -> None:
    """Export jobs to markdown/json/csv."""
    from jobtracker.services import export_service

    with _session_scope() as session:
        path = export_service.export(session, fmt, output=output)
    typer.echo(f"Exported {fmt} to {path}.")


@app.command("dashboard")
def dashboard() -> None:
    """Launch the Streamlit dashboard."""
    import subprocess
    import sys

    app_path = Path(__file__).resolve().parent / "dashboard" / "app.py"
    typer.echo(f"Launching Streamlit dashboard ({app_path}).")
    subprocess.run(
        [sys.executable, "-m", "streamlit", "run", str(app_path)], check=False
    )


@app.command("health")
def health() -> None:
    """Show source health and the accuracy report."""
    from jobtracker.services import health_service

    with _session_scope() as session:
        for row in health_service.source_health(session):
            typer.echo(
                f"{row.company_id}\tstatus={row.last_status}\t"
                f"jobs={row.current_job_count}\terrors={row.error_count or 0}"
            )
        report = health_service.accuracy_report(session)
    typer.echo("")
    typer.echo("Accuracy report:")
    typer.echo(f"  missing application URL: {len(report.missing_application_url)}")
    typer.echo(f"  unknown term: {len(report.unknown_term)}")
    typer.echo(f"  unknown sponsorship: {len(report.unknown_sponsorship)}")
    typer.echo(f"  low-confidence AI: {len(report.low_confidence_ai)}")
    typer.echo(f"  stale sources: {len(report.stale_sources)}")
    typer.echo(f"  removed-but-present: {len(report.removed_but_present)}")
    typer.echo(f"  active-but-absent: {len(report.active_but_absent)}")


@app.command("test-source")
def test_source(company_id: str = typer.Argument(...)) -> None:
    """Test connectivity for a single company's source."""
    from jobtracker.connectors.http import HttpClient
    from jobtracker.services import company_service

    with _session_scope() as session, HttpClient() as http:
        result = company_service.test_source(session, company_id, http)
    status = "ok" if result.ok else "failed"
    typer.echo(
        f"test-source {company_id}: {status} "
        f"(ats={result.ats}, fetched={result.fetched})"
    )
    if result.error:
        typer.echo(f"  error: {result.error}")


@app.command("backup")
def backup(
    output: str = typer.Option(..., "--output", help="Destination path for the backup"),
) -> None:
    """Back up the SQLite database to ``--output`` (a safe file copy).

    Uses SQLite's online backup API so it is safe even while the app is
    running. Only SQLite URLs are supported; for other databases use that
    engine's native dump tooling.
    """
    from jobtracker.services import backup_service

    dest = backup_service.backup_database(get_settings(), output)
    typer.echo(f"Backed up database to {dest}.")


@app.command("restore")
def restore(
    input: str = typer.Option(..., "--input", help="Path to a backup to restore from"),
) -> None:
    """Restore the SQLite database from a backup created by ``backup``.

    Overwrites the current database file with the backup. Stop the app/scheduler
    first. Only SQLite URLs are supported.
    """
    from jobtracker.services import backup_service

    dest = backup_service.restore_database(get_settings(), input)
    typer.echo(f"Restored database from backup into {dest}.")


@app.command("scheduler")
def scheduler() -> None:
    """Run the foreground polling scheduler (dedicated worker process).

    Does an immediate first poll of all enabled + active companies on startup,
    then polls each company on its effective interval (priority default, capped
    so no company ever waits longer than ~1 hour). Runs until interrupted. Keep
    the one-shot ``run`` command for manual single passes.
    """
    from jobtracker.scheduler import run_scheduler

    settings = get_settings()
    typer.echo(
        "Starting scheduler (immediate first poll, then interval polling; "
        f"max interval {settings.scheduler_max_interval_minutes} min). "
        "Press Ctrl+C to stop."
    )
    run_scheduler(settings)


@app.command("analyze")
def analyze(
    job_id: str = typer.Option(..., "--job", help="Job id to analyze with the AI engine"),
    force: bool = typer.Option(
        False, "--force", help="Re-run even if a cached analysis exists"
    ),
) -> None:
    """Run the AI relevance engine on one job (safe with no API key).

    With no Gemini key configured this prints an informative 'add a key'
    message instead of failing. With a key it analyzes the job, caches the
    result (keyed by description + profile version), and prints a summary.
    """
    from jobtracker.services import ai_service

    with _session_scope() as session:
        result = ai_service.analyze_job(session, job_id, force=force)

    if not result.available:
        typer.echo(result.message or "AI unavailable - add a Gemini API key.")
        return

    data = result.result
    source = "cache" if result.cache_hit else f"{result.provider}:{result.model}"
    typer.echo(
        f"analyze {job_id} [{source}]: "
        f"score={data.get('relevance_score')} "
        f"recommendation={data.get('recommendation')} "
        f"confidence={result.confidence}"
    )
    if data.get("explanation"):
        typer.echo(f"  {data['explanation']}")


@app.command("notify")
def notify(
    since_hours: float | None = typer.Option(
        None,
        "--since-hours",
        help="Only consider jobs/events detected in the last N hours (default: all).",
    ),
) -> None:
    """Email users about recent new/changed/removed jobs and source failures.

    Evaluates each user's subscriptions and notification preferences against
    recently detected changes and sends due emails, deduped so nothing is sent
    twice. Safe to run with no SMTP creds: emails are written to the outbox.
    """
    from datetime import UTC, datetime, timedelta

    from jobtracker.services import notification_service

    since = None
    if since_hours is not None:
        since = datetime.now(UTC) - timedelta(hours=since_hours)

    with _session_scope() as session:
        result = notification_service.run_recent(session, since=since)
    typer.echo(
        f"Notify complete: {result.sent} sent, "
        f"{result.skipped_duplicate} skipped (already sent)."
    )


@app.command("digest")
def digest(
    since_hours: float = typer.Option(
        24.0,
        "--since-hours",
        help="Summarise jobs detected in the last N hours (default: 24).",
    ),
) -> None:
    """Send each subscribed user a periodic summary email of recent matches.

    Safe to run with no SMTP creds: emails are written to the outbox.
    """
    from datetime import UTC, datetime, timedelta

    from jobtracker.services import notification_service

    since = datetime.now(UTC) - timedelta(hours=since_hours)
    with _session_scope() as session:
        result = notification_service.send_digest(session, since=since)
    typer.echo(
        f"Digest complete: {result.sent} sent, "
        f"{result.skipped_duplicate} skipped (already sent)."
    )


if __name__ == "__main__":
    app()
