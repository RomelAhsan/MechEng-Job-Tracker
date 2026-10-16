# MechEng-Job-Tracker

A local-first tracker for mechanical-engineering co-op / internship postings. It
aggregates company career pages (ATS boards) into one searchable SQLite database,
applies deterministic, evidence-first screening for a University of Waterloo
Mechanical Engineering student, and surfaces everything through a Streamlit
dashboard.

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
HTTP settings, etc.). **No AI/API key is required to run anything.**

## Quick start

```bash
python -m jobtracker init-db                 # create/upgrade the schema
python -m jobtracker run                     # ingest enabled companies
python -m jobtracker screen                  # classify/extract/score stored jobs
python -m jobtracker dashboard               # open the Streamlit dashboard
```

## Architecture overview

The project is **backend-first and frontend-replaceable**: all business logic
lives in the `jobtracker` service and pure-function layers, so the Streamlit UI
(and a future FastAPI/React app) is a thin presentation layer that only calls
services.

```
jobtracker/
  settings.py        # pydantic-settings configuration
  cli.py             # Typer CLI (full SPEC command surface)
  config/            # YAML loaders (companies.yaml, user.yaml)
  schemas/           # Pydantic v2 models (CanonicalJob, CompanyConfig, UserProfile)
  db/                # SQLAlchemy 2.0 models + repositories + engine/session
  connectors/        # shared HTTP client + ATS connectors + fallback stub
  normalize/         # HTML cleaning, RawJob -> CanonicalJob, hashing, quarantine gate
  detect/            # new/changed/removed/reactivated/expired detection + events
  extract/           # term & duration, citizenship & sponsorship extraction
  screen/            # mechanical-relevance classifier + fit-score
  services/          # orchestration: ingest, company, screening, export, health,
                     #                dashboard (read-side query service)
  dashboard/         # Streamlit entry (app.py) + pages/ (thin views)
migrations/          # Alembic env + single hand-authored 0001_initial revision
config/              # companies.yaml (target list) + user.yaml (profile)
tests/               # pytest suite with mocked ATS fixtures (no live network)
```

Data flow: `connectors → normalize → detect → screen/extract → db → dashboard`.
YAML seeds the database; the database is authoritative at runtime. Evidence and
`unknown` are first-class for term, sponsorship, and classification — the system
never guesses and never silently hides ambiguous mechanical-engineering roles
(adjacent roles become `possible_match` with a reason). Ingestion is fail-soft:
one source failing never aborts a run; failures and health are recorded in
`source_health`, and payloads missing an application/source URL are routed to a
`quarantine` queue rather than dropped.

## Dashboard

`python -m jobtracker dashboard` shells out to
`streamlit run jobtracker/dashboard/app.py`. The dashboard has four Phase-1
pages, each a thin view over the service layer:

1. **Relevant For Me** (default) — strong + possible matches with new-job
   badges, fit score, concerns, apply + source links, and filters (term,
   duration, location, country, remote, employment type, sponsorship
   eligibility, freshness window, fit-score range, classification). Save / AI
   buttons are rendered but disabled (later phases).
2. **All Internships** — the full active database with advanced filters, a
   "Show removed (archive)" toggle, source info, and direct links.
3. **Companies** — a table with source-health summary, an add-by-URL form, and
   edit / enable / disable / remove plus priority / interval controls (all
   writes go through `company_service`).
4. **Source Health** — last check times, run counts, errors, source status, the
   quarantine queue, and the accuracy report.

## Configuration

Configuration comes from environment variables / `.env` (see `.env.example`) and
two YAML files:

- `config/companies.yaml` — the tracked employer list. Each entry has `id`,
  `name`, `ats` (`greenhouse`/`lever`/`ashby`/`smartrecruiters`/`workable`/`rss`/
  `sitemap`/`json_ld`/`html`), optional `board_token`, `careers_url`, `enabled`,
  `priority` (`high`/`medium`/`low`), `check_interval_minutes`, `locations`, and
  `notes`. Unsupported ATS types route to a fallback stub (`requires_fallback`).
- `config/user.yaml` — the single implicit Phase-1 user profile (citizenship,
  work authorization, available terms, preferred duration, interests, skills).

Key settings (`jobtracker/settings.py`): `database_url` (default
`sqlite:///./jobtracker.db`), `log_level`, `log_file`, `companies_file`,
`user_file`, `http_timeout`, `http_max_retries`, `rate_limit_per_host`. The
`discord_webhook_url` and `ai_provider`/`ai_api_key` settings exist but are
unused in Phase 1.

## CLI commands

| Command | Description |
| --- | --- |
| `init-db` | Create or upgrade the database to the latest Alembic revision (idempotent). |
| `add-company URL [--id --name --priority]` | Detect a company's ATS from a careers URL, save it, and baseline-import. |
| `list-companies` | List tracked companies (seeds from `companies.yaml` first). |
| `enable-company ID` | Re-enable a company for ingestion. |
| `disable-company ID` | Disable a company (kept in the database). |
| `remove-company ID` | Mark a company inactive + disabled (history retained). |
| `run [--company ID]` | Fail-soft ingestion pass over enabled companies. |
| `screen` | Classify, extract, and score stored jobs. |
| `export --format {markdown\|json\|csv} [--output PATH]` | Export active jobs to `output/`. |
| `dashboard` | Launch the Streamlit dashboard. |
| `health` | Print source health and the accuracy report. |
| `test-source ID` | Probe a single company's source connectivity (fail-soft). |
| `analyze` / `notify` / `digest` | Registered but deferred — print an "available in a later phase" message. |

Run `python -m jobtracker --help` for the full surface.

## Tests & linting

```bash
pytest -q
ruff check jobtracker tests
```

Unit tests perform no live network I/O; ATS responses are mocked with fixtures.

## Troubleshooting

- **`pip install` fails with a read-only filesystem error** — install inside a
  virtual environment (`python -m venv .venv && . .venv/bin/activate`) before
  running `pip install -e ".[dev]"`.
- **CLI reports no companies / no jobs** — run `python -m jobtracker init-db`,
  then `run`, then `screen`; `list-companies`/`run` seed from `companies.yaml`.
- **A company shows `requires_fallback`** — its ATS is not one of the four
  supported connectors yet; it is tracked but not fetched until a connector
  exists (expected for `ats: html` entries).
- **The dashboard shows empty pages** — point `DATABASE_URL` at a database that
  has been through `init-db` + `run` + `screen`, or seed data first.
- **`streamlit` command not found** — ensure the virtual environment is active;
  Streamlit is installed as a runtime dependency.

## Phase boundary

Phase 0/1 covers the project scaffold, database schema and migrations, the CLI
surface, connectors, normalization, detection, deterministic screening, export,
source health, and the Streamlit dashboard. AI relevance/analysis, resume
generation, authentication/multi-user, Discord notifications, and deployment /
scheduling are deferred to later phases. Multi-user tables exist as nullable,
unused scaffolds and are not wired to any authentication.
