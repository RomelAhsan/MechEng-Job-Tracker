# Implementation Plan — Phase 0 & Phase 1

Execute top-to-bottom. Each task is incremental, test-backed, and references the
requirements it satisfies. **Do not start until the user approves this plan.**
No AI, auth, resume, notifications, or deployment code in this plan.

---

## Phase 0 — Scaffold

- [ ] 1. Initialize Python project and dependencies
  - Create `pyproject.toml` (Python 3.11+, package `jobtracker`) with runtime
    deps (fastapi, sqlalchemy>=2.0, alembic, pydantic>=2, pydantic-settings,
    httpx, feedparser, beautifulsoup4, lxml, pyyaml, typer, streamlit) and dev
    deps (pytest, respx/pytest-httpx, ruff).
  - Add `.gitignore` (venv, `*.db`, `output/`, `__pycache__`, `.env`).
  - Add `.env.example` and `jobtracker/settings.py` (Pydantic-settings).
  - _Requirements: 1.1, 1.2_

- [ ] 2. Package skeleton and entrypoints
  - Create `jobtracker/__init__.py`, `__main__.py`, and empty subpackages per the
    design layout (config, schemas, db, connectors, normalize, detect, extract,
    screen, services, dashboard, notifications).
  - Create `jobtracker/logging_setup.py` and wire it from the CLI.
  - Create `jobtracker/cli.py` Typer app registering ALL SPEC commands (deferred
    ones print a "available in Phase 3/5" message); `python -m jobtracker --help`
    lists them.
  - _Requirements: 1.1, 1.4, 2.11_

- [ ] 3. Database base + models + migrations
  - `jobtracker/db/base.py`: DeclarativeBase, engine/session from settings.
  - `jobtracker/db/models.py`: Company, Job (canonical schema), JobEvent,
    SourceHealth, QuarantineItem; scaffold User/SavedJob/UserProfile(+
    profile_version)/NotificationLog (nullable, unused).
  - Configure Alembic (`alembic.ini`, `migrations/env.py` reading settings) and
    author `0001_initial` creating all tables (portable types only).
  - Implement `init-db` to run `alembic upgrade head`.
  - _Requirements: 1.3, 2.9, 3.1 (deferred scaffolds)_

- [ ] 4. Testing harness + README skeleton
  - `tests/conftest.py` (in-memory/temp SQLite fixture, session fixture),
    `tests/fixtures/` dir, a smoke test that `init-db` builds the schema.
  - Draft README: install, init-db, run, screen, dashboard, test, phase boundary.
  - _Requirements: 1.5, 1.6_

- [ ] **Phase 0 review checkpoint** — stop for user review before Phase 1.

---

## Phase 1 — Core ingestion & dashboard

### Config & schemas

- [ ] 5. Pydantic schemas and config loaders
  - `schemas/job.py` (CanonicalJob + enums for status/term/employment/remote/
    classification/visa), `schemas/company.py` (CompanyConfig + ats enum),
    `schemas/user_profile.py` (UserProfile).
  - `config/companies_loader.py` and `config/user_loader.py` with validation and
    friendly errors.
  - Seed `config/companies.yaml` with a small verified set (3–5 confirmed public
    ATS boards) and `config/user.yaml` (SPEC profile).
  - Tests: valid/invalid YAML parsing.
  - _Requirements: 2.1, 2.8; design §16.3_

### Normalization & hashing

- [ ] 6. HTML cleaning, normalizer, and description hashing
  - `normalize/html_clean.py`, `normalize/normalizer.py` (RawJob→CanonicalJob:
    location split, remote/employment inference, requirements/skills extraction),
    `normalize/hashing.py` (SHA-256 description_hash).
  - Validation gate routing missing-URL jobs to quarantine.
  - Tests: normalization against fixtures, stable hashing, quarantine on missing
    application_url.
  - _Requirements: 2.3, 2.13_

### Connectors

- [ ] 7. Connector base, shared HTTP, registry + ATS detection
  - `connectors/base.py` (Connector ABC, RawJob), `connectors/http.py` (httpx
    client, exponential backoff on 429/5xx/transport, per-host rate limit),
    `connectors/registry.py` (ats→connector, `detect_ats(url)`),
    `connectors/fallback.py` (workable/rss/sitemap/json_ld/html → requires_fallback).
  - Tests: backoff/retry logic (mocked), `detect_ats` for each supported domain,
    fallback marks requires_fallback without raising.
  - _Requirements: 2.2, 2.14_

- [ ] 8. Greenhouse connector
  - `connectors/greenhouse.py` + fixture + test (normalize, application/source
    URL preserved, raw payload stored).
  - _Requirements: 2.2, 2.3_

- [ ] 9. Lever connector
  - `connectors/lever.py` + fixture + test.
  - _Requirements: 2.2, 2.3_

- [ ] 10. Ashby connector
  - `connectors/ashby.py` + fixture + test (respect `isListed`).
  - _Requirements: 2.2, 2.3_

- [ ] 11. SmartRecruiters connector
  - `connectors/smartrecruiters.py` (pagination via offset/limit/totalFound) +
    fixture + test.
  - _Requirements: 2.2, 2.3_

### Detection

- [ ] 12. New/changed/removed/active/expired detection
  - `detect/diff.py` keyed on (company_id, source_job_id); emits JobEvents;
    updates source_health counts; baseline mode suppresses new events.
  - Tests: new, changed (hash differs), removed, reactivated, baseline-no-false-new.
  - _Requirements: 2.4, 2.9_

### Extraction

- [ ] 13. Term & duration extraction
  - `extract/term.py` with season map + duration parsing + evidence; unknown when
    undetermined (no guessing).
  - Tests: ALL SPEC example phrases ("4-month Spring 2027 co-op", "May-August
    2027", "12-month internship beginning May 2027", "Winter 2027 work term",
    "Fall 2026 co-op", "September to December") + an unknown case.
  - _Requirements: 2.6_

- [ ] 14. Citizenship & sponsorship extraction
  - `extract/work_auth.py` producing citizenship/work-auth/visa_sponsorship/
    confidence/evidence + Canadian-citizen eligibility rules.
  - Tests: Canadian-eligible, US-no-sponsorship → likely_ineligible, US-
    sponsorship-provided → eligible, missing-info → possibly_eligible_unknown,
    evidence always present or unknown.
  - _Requirements: 2.7, 2.13_

### Screening

- [ ] 15. Mechanical relevance classifier
  - `screen/signals.py` (SPEC lists), `screen/meche_classifier.py`
    (strong/possible/weak/not_relevant, recall-first, reasons+confidence).
  - Tests: strong title, adjacent→possible (not rejected), clearly-unrelated→
    not_relevant only when title+description unrelated.
  - _Requirements: 2.5_

- [ ] 16. Fit score & screening service
  - `screen/fit_score.py` (SPEC weights, hard/soft filters, reasons/concerns/
    missing/evidence/confidence) and `services/screening_service.py` to persist
    classification + extraction + score onto jobs.
  - Tests: weighted score math, hard-filter zeroing, result completeness.
  - _Requirements: 2.5, 2.6, 2.7, 2.8_

### Services & CLI

- [ ] 17. Company service (sync/add/enable/disable/remove)
  - `services/company_service.py`: `sync_from_yaml`, `add_from_url`
    (detect→test endpoint→save→baseline import→mark seen), enable/disable/remove
    (remove keeps history), list.
  - Wire CLI: `list-companies`, `enable-company`, `disable-company`,
    `remove-company`, `add-company`, `test-source`.
  - Tests: add-company baseline suppresses false-new; remove keeps history.
  - _Requirements: 2.1, 2.11_

- [ ] 18. Ingest service + run/screen CLI
  - `services/ingest_service.py` orchestrating fetch→normalize→validate→diff→
    persist→source_health→screen, per-company fail-soft.
  - Wire CLI `run`, `run --company`, `screen`.
  - Tests: one failing company doesn't abort the run; source_health updated on
    success and error.
  - _Requirements: 2.2, 2.3, 2.4, 2.14, 2.11_

- [ ] 19. Export service + export CLI
  - `services/export_service.py` (markdown/json/csv with SPEC export fields +
    profile summary) and `export` CLI command.
  - Tests: each format contains required fields.
  - _Requirements: 2.11_

- [ ] 20. Health service + accuracy report + health CLI
  - `services/health_service.py` (source-health view + accuracy report queries)
    and `health` CLI command.
  - Tests: accuracy report flags missing URL / unknown term / unknown
    sponsorship / stale source / removed-active mismatch.
  - _Requirements: 2.13_

### Company list population

- [ ] 20b. Populate full target company list in `config/companies.yaml`
  - For each user-provided target company, detect/verify ATS + board token via
    `detect_ats` against the real careers URL (web research allowed here, NOT in
    unit tests). Confirmed public boards get the right connector; unconfirmed
    ones get the detected fallback type + `requires_fallback`. No fabricated
    tokens. Apply the priority heuristic from design §16.3.
  - _Requirements: 2.1; design §16.3_

### Dashboard

- [ ] 21. Streamlit dashboard (Phase 1 pages)
  - `dashboard/app.py` + pages: Relevant For Me (default; matches, badges, score,
    concerns, apply+source links, filters), All Internships (+ removed archive
    toggle, advanced filters), Companies (add/edit/enable/disable/remove + health),
    Source Health (checks/counts/errors + quarantine + accuracy report).
  - Wire `dashboard` CLI to `streamlit run`.
  - Verify by launching Streamlit against a seeded SQLite DB and confirming pages
    render, filters work, and apply/source links resolve to real URLs.
  - _Requirements: 2.10, 2.11_

### Wrap-up

- [ ] 22. Finalize README + full test pass
  - Expand README (architecture overview, config, all commands, troubleshooting,
    Phase boundary note). Run `pytest -q` green and `ruff` clean.
  - _Requirements: 1.6, 2.12_

- [ ] **Phase 1 review checkpoint** — stop for user review (per SPEC, do not start
  Phase 2+ until reviewed).

---

### Traceability summary
- Phase 0 scaffold → Reqs 1.1–1.6
- Connectors/ingestion/detection → Reqs 2.1–2.4, 2.9, 2.14
- Classifier/extraction/scoring → Reqs 2.5–2.8
- Dashboard/CLI/export/health → Reqs 2.10, 2.11, 2.13
- Tests throughout → Req 2.12
