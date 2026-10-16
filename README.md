# MechEng-Job-Tracker

A local-first tracker for mechanical-engineering co-op / internship postings,
aggregating company career pages (ATS boards) into one searchable database with
deterministic screening and a Streamlit dashboard.

This repository currently implements **Phase 0 and Phase 1**. AI screening,
resume generation, authentication/multi-user, Discord notifications, and
deployment are intentionally deferred to later phases.

## Requirements

- Python 3.11+

## Install

From the repository root:

```bash
pip install -e ".[dev]"
```

Copy `.env.example` to `.env` to override defaults (database URL, log level,
HTTP settings, etc.). No AI/API key is required to run anything.

## Initialize the database

```bash
python -m jobtracker init-db
```

This runs the Alembic migrations (`alembic upgrade head`) to create or upgrade
`jobtracker.db`. It is idempotent — re-running leaves the schema at head.

## Common commands

```bash
python -m jobtracker --help            # list all commands
python -m jobtracker run               # ingest enabled companies
python -m jobtracker screen            # classify/extract/score stored jobs
python -m jobtracker export --format markdown
python -m jobtracker dashboard         # launch the Streamlit dashboard
python -m jobtracker health            # source health + accuracy report
```

Deferred commands (`analyze`, `notify`, `digest`) are registered but only print
an "available in a later phase" message.

## Tests

```bash
pytest -q
ruff check jobtracker tests
```

Unit tests perform no live network I/O; ATS responses are mocked.

## Phase boundary

Phase 0/1 covers the project scaffold, database schema and migrations, the CLI
surface, connectors, normalization, detection, deterministic screening, and the
Streamlit dashboard. Multi-user tables exist as nullable, unused scaffolds and
are not wired to any authentication.
