"""Enable ``python -m jobtracker`` to invoke the Typer CLI."""

from jobtracker.cli import app

if __name__ == "__main__":
    app()
