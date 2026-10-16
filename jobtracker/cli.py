"""Typer CLI exposing the full SPEC command surface.

Phase 1 implements scaffolding stubs for most commands; the deferred
commands (analyze/notify/digest) only print an 'available in a later
phase' message. ``init-db`` runs Alembic migrations programmatically.
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

_LATER_PHASE_MSG = "This command is available in a later phase."


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


@app.command("analyze")
def analyze() -> None:
    """AI analysis (deferred to a later phase)."""
    typer.echo(_LATER_PHASE_MSG)


@app.command("notify")
def notify() -> None:
    """Notifications (deferred to a later phase)."""
    typer.echo(_LATER_PHASE_MSG)


@app.command("digest")
def digest() -> None:
    """Digest summaries (deferred to a later phase)."""
    typer.echo(_LATER_PHASE_MSG)


if __name__ == "__main__":
    app()
