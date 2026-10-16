"""Typer CLI exposing the full SPEC command surface.

Phase 1 implements scaffolding stubs for most commands; the deferred
commands (analyze/notify/digest) only print an 'available in a later
phase' message. ``init-db`` runs Alembic migrations programmatically.
"""

from __future__ import annotations

from pathlib import Path

import typer

from jobtracker.logging_setup import configure
from jobtracker.settings import get_settings

app = typer.Typer(help="Waterloo Mechanical Engineering Co-op Job Tracker")

_LATER_PHASE_MSG = "This command is available in a later phase."


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
def add_company(url: str = typer.Argument(..., help="Careers URL to detect and add")) -> None:
    """Add a company by detecting its ATS from a careers URL."""
    typer.echo("add-company is not implemented yet.")


@app.command("list-companies")
def list_companies() -> None:
    """List tracked companies."""
    typer.echo("list-companies is not implemented yet.")


@app.command("enable-company")
def enable_company(company_id: str = typer.Argument(...)) -> None:
    """Enable a company for ingestion."""
    typer.echo("enable-company is not implemented yet.")


@app.command("disable-company")
def disable_company(company_id: str = typer.Argument(...)) -> None:
    """Disable a company (kept in the database)."""
    typer.echo("disable-company is not implemented yet.")


@app.command("remove-company")
def remove_company(company_id: str = typer.Argument(...)) -> None:
    """Remove a company (marks inactive; history kept)."""
    typer.echo("remove-company is not implemented yet.")


@app.command("run")
def run(company: str | None = typer.Option(None, "--company", help="Limit to one company")) -> None:
    """Run an ingestion pass over enabled companies."""
    typer.echo("run is not implemented yet.")


@app.command("screen")
def screen() -> None:
    """Classify, extract, and score stored jobs."""
    typer.echo("screen is not implemented yet.")


@app.command("export")
def export(
    fmt: str = typer.Option("markdown", "--format", help="markdown|json|csv"),
    output: str | None = typer.Option(None, "--output", help="Output path"),
) -> None:
    """Export jobs to markdown/json/csv."""
    typer.echo("export is not implemented yet.")


@app.command("dashboard")
def dashboard() -> None:
    """Launch the Streamlit dashboard."""
    typer.echo("dashboard is not implemented yet.")


@app.command("health")
def health() -> None:
    """Show source health and the accuracy report."""
    typer.echo("health is not implemented yet.")


@app.command("test-source")
def test_source(company_id: str = typer.Argument(...)) -> None:
    """Test connectivity for a single company's source."""
    typer.echo("test-source is not implemented yet.")


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
