# Requirements — Phase 0 & Phase 1

Feature: Waterloo Mechanical Engineering Co-op Job Tracker — scaffold (Phase 0)
and core ingestion + dashboard (Phase 1).

Source of truth: `JobAggregateSPEC.md` at the repo root.

Scope boundary for this spec: **Phase 0 and Phase 1 only.** Authentication, AI
relevance analysis, resume generation, notifications, and deployment are
**out of scope** here and are listed in a "Deferred" section at the end so the
Phase 1 code is structured not to block them.

---

## 1. Phase 0 — Project scaffold

### Requirement 1.1 — Repository structure
**User story:** As a developer, I want a clean, modular Python package layout so
the backend can be reused by a future React frontend without rewriting it.

Acceptance criteria:
1. WHEN the repo is set up THEN a `jobtracker/` Python package SHALL exist with
   clearly separated subpackages for config, connectors, normalization,
   detection, screening, extraction, persistence (db/models/repositories),
   services, CLI, and dashboard.
2. WHEN business logic is written THEN it SHALL live outside the Streamlit
   dashboard module so the dashboard is a thin presentation layer over a service
   API.
3. WHEN the package is imported THEN `python -m jobtracker --help` SHALL list the
   CLI commands.

### Requirement 1.2 — Dependencies & environment
**User story:** As a developer, I want pinned dependencies and reproducible
environment configuration.

Acceptance criteria:
1. The project SHALL declare dependencies via `pyproject.toml` targeting
   Python 3.11+ and include: fastapi, sqlalchemy>=2.0, alembic, pydantic>=2,
   pydantic-settings, httpx, feedparser, beautifulsoup4, lxml, pyyaml, typer,
   streamlit, pytest, pytest-httpx (or respx), and tooling (ruff, mypy optional).
2. Configuration SHALL be read from environment variables and a `.env` file via
   Pydantic settings, with a committed `.env.example`.
3. Secrets (Discord webhook, AI keys) SHALL be referenced from config but are
   NOT required for Phase 1 to run.

### Requirement 1.3 — Database migrations
**User story:** As a developer, I want Alembic migrations against a
PostgreSQL-compatible schema that runs on SQLite locally.

Acceptance criteria:
1. WHEN `python -m jobtracker init-db` is run THEN the SQLite database SHALL be
   created/upgraded to the latest Alembic revision.
2. The schema SHALL avoid SQLite-only types and SHALL use portable column types
   (String, Text, Integer, Float, Boolean, DateTime(timezone), JSON) so the same
   models target PostgreSQL later.
3. An initial Alembic migration SHALL create all Phase 1 tables.

### Requirement 1.4 — Logging
Acceptance criteria:
1. The application SHALL configure structured logging with levels controlled by
   config, writing to stdout and optionally a rotating file.
2. Each ingestion run SHALL log start, per-company results, and errors with
   company id context.

### Requirement 1.5 — Testing setup
Acceptance criteria:
1. `pytest` SHALL be runnable with `pytest -q` from the repo root.
2. Tests SHALL use local fixture files; no live network calls in unit tests
   (enforced via mocked httpx transport / respx).
3. A `tests/fixtures/` directory SHALL hold sample ATS API responses.

### Requirement 1.6 — README
Acceptance criteria:
1. The README SHALL document install, init-db, add-company, run, screen,
   dashboard, and test commands, plus the Phase boundary.

---

## 2. Phase 1 — Core ingestion & dashboard

### Requirement 2.1 — Company registry
**User story:** As a user, I want companies configured in YAML and editable via
the dashboard, with removal preserving history.

Acceptance criteria:
1. The system SHALL read `config/companies.yaml` with the entry shape defined in
   the SPEC (id, name, ats, board_token, careers_url, enabled, priority,
   check_interval_minutes, locations, notes).
2. WHEN companies are synced THEN YAML entries SHALL be upserted into the
   `companies` table (YAML is the seed/source; dashboard edits persist to DB).
3. The system SHALL support ATS values: greenhouse, lever, ashby,
   smartrecruiters, workable, rss, sitemap, json_ld, html. **Phase 1 implements
   greenhouse, lever, ashby, smartrecruiters connectors**; the remaining values
   SHALL be accepted in config and routed to a "fallback/unsupported" stub that
   marks the source as needing a fallback (no crash).
4. WHEN a company is removed THEN future checks SHALL be disabled and the source
   marked inactive, and historical jobs SHALL be retained.
5. Users SHALL be able to add, edit, enable, disable, and remove companies from
   both YAML and the dashboard.
6. Priority SHALL map to default polling intervals: high=15 min, medium=30-60
   min, low=6-24 h (defaults applied when `check_interval_minutes` absent).

### Requirement 2.2 — ATS connectors
**User story:** As a user, I want public ATS APIs ingested reliably with no
scraping of auth-walled content.

Acceptance criteria:
1. Connectors SHALL be implemented for Greenhouse, Lever, Ashby, SmartRecruiters
   using the exact public endpoints in the SPEC.
2. Each connector SHALL: normalize to the canonical schema, handle pagination,
   respect rate limits, retry transient failures with exponential backoff,
   preserve the original application URL and source URL, preserve
   source-specific metadata, and store raw payload for debugging.
3. Connectors SHALL NOT bypass login walls, CAPTCHAs, or access controls.
4. WHEN a connector fails THEN the run SHALL continue for other companies and
   the failure SHALL be recorded in source health (error_count, last_error).
5. The Workable value and non-API ATS values SHALL be handled by a fallback stub
   that records "requires fallback" rather than raising (full implementation
   deferred).

### Requirement 2.3 — Job normalization
Acceptance criteria:
1. Every ingested job SHALL be normalized into the canonical job schema fields
   defined in the SPEC.
2. A `description_hash` SHALL be computed deterministically from normalized
   description + key metadata.
3. Every active job SHALL have a non-empty `application_url` and `source_url`;
   jobs failing this SHALL be routed to the quarantine queue, not silently
   dropped.

### Requirement 2.4 — New / changed / removed / active / expired detection
Acceptance criteria:
1. A job SHALL be `new` when its (company_id, source_job_id) pair has never been
   seen.
2. A job SHALL be `changed` when its description_hash or key metadata differs
   from the stored record.
3. A job SHALL be `removed` when previously active but absent from the current
   source listing; `removed_at` SHALL be set and it SHALL be excluded from
   default active views but remain in the archive.
4. A job SHALL be `active` when present in the current listing.
5. A job SHALL be `expired` when its application deadline has passed (when a
   deadline is known).
6. Each run SHALL record per-source counts: new_jobs_last_run,
   removed_jobs_last_run, current_job_count.
7. Baseline import (first add) SHALL mark existing jobs as already seen so they
   do NOT appear as new (supports the add-company assistant requirement).

### Requirement 2.5 — Mechanical relevance classifier
**User story:** As a Mechanical Engineering student, I want recall-first
classification that never silently hides ambiguous roles.

Acceptance criteria:
1. The classifier SHALL assign `strong_match`, `possible_match`, `weak_match`,
   or `not_relevant` using the SPEC's title/skill/adjacent/negative signals.
2. Adjacent roles SHALL be classified at least `possible_match` with a reason,
   never auto-rejected.
3. A job SHALL be `not_relevant` ONLY when both title and description clearly
   indicate an unrelated field (per SPEC negative-signal rule).
4. Every classification SHALL include `classification_reasons` and a
   `classification_confidence`.

### Requirement 2.6 — Term & duration extraction
Acceptance criteria:
1. Deterministic (rule-based) extraction SHALL run first and populate
   `term_season`, `term_start_date`, `term_end_date`, `duration_months`,
   `duration_label`.
2. Season→date mapping SHALL follow the SPEC table (Spring/Summer May 1–Aug 31,
   Fall Sep 1–Dec 31, Winter Jan 1–Apr 30).
3. The SPEC example phrases SHALL be correctly parsed (listed as test cases).
4. WHEN the term cannot be determined THEN `term_season` SHALL be `unknown` and
   the job SHALL be flagged for a "Needs term information" filter. The system
   SHALL NOT guess.
5. Every non-unknown term conclusion SHALL carry evidence (the matched text).

### Requirement 2.7 — Citizenship & sponsorship extraction
Acceptance criteria:
1. A dedicated classifier SHALL populate `citizenship_requirement`,
   `work_authorization_requirement`, `visa_sponsorship`
   (provided|not_provided|unknown), `sponsorship_confidence`, and
   `sponsorship_evidence` (exact quote).
2. For a Canadian-citizen profile: Canadian roles SHALL be eligible unless they
   explicitly require another country's PR/citizenship; U.S. roles SHALL be
   eligible only if sponsorship is explicitly provided or Canadian work auth is
   explicitly accepted; U.S. roles stating no sponsorship SHALL be
   `likely_ineligible_sponsorship_required`; missing info SHALL be
   `possibly_eligible_unknown` (never auto-ineligible).
3. Every sponsorship conclusion SHALL include evidence or be `unknown`.

### Requirement 2.8 — Fit score & screening
Acceptance criteria:
1. A fit score 0–100 SHALL be computed using the SPEC weights (mechanical 30,
   type 15, spring term 15, duration 10, location 10, citizenship/sponsorship
   10, skill/interest 10).
2. Hard filters (removed/expired, incompatible term/duration/location/
   citizenship) SHALL exclude a job from "Relevant For Me".
3. Each screening result SHALL include score, matched reasons, concerns, missing
   information, evidence quotes, and a confidence score.
4. The active user profile SHALL come from `config/user.yaml` (single-user in
   Phase 1; multi-user tables scaffolded but not wired to auth).

### Requirement 2.9 — Persistence (SQLite, PG-compatible)
Acceptance criteria:
1. Jobs, companies, source-health, job-history/events, and a quarantine table
   SHALL be persisted.
2. Removed jobs SHALL be retained with full history.
3. Schema SHALL be portable to PostgreSQL (per Req 1.3).

### Requirement 2.10 — Basic Streamlit dashboard
Acceptance criteria:
1. The dashboard SHALL provide, at minimum for Phase 1: **Relevant For Me**,
   **All Internships** (with removed-jobs archive), **Companies**, and
   **Source Health** pages.
2. "Relevant For Me" SHALL show strong + possible matches with new-job badges,
   fit score, apply link, and the core filters (term, duration, location,
   sponsorship, freshness, score, classification).
3. Every job row SHALL expose a direct application link and source link.
4. Companies page SHALL add/edit/enable/disable/remove companies and show source
   health; Source Health page SHALL show last check times, counts, errors, and
   quarantined records.
5. Save/heart, AI-analysis buttons, and application-pipeline UI MAY be stubbed or
   deferred to Phase 2/3 but SHALL NOT break the page.

### Requirement 2.11 — CLI
Acceptance criteria:
1. The following CLI commands SHALL work in Phase 1: `init-db`, `add-company`,
   `list-companies`, `enable-company`, `disable-company`, `remove-company`,
   `run`, `run --company`, `screen`, `export`, `dashboard`, `health`,
   `test-source`.
2. `analyze`, `notify`, `digest` SHALL exist as registered commands but MAY emit
   a "not available until Phase 3/5" message (so the CLI surface matches the SPEC
   without implementing deferred features).
3. `add-company <url>` SHALL detect ATS, extract token, test the endpoint, save
   the company, run a baseline import, and mark existing jobs seen.
4. `export --format {markdown|json|csv}` SHALL export jobs with the SPEC's export
   fields.

### Requirement 2.12 — Tests
Acceptance criteria:
1. Tests SHALL cover (using fixtures, no network): each implemented connector,
   normalization, new/changed/removed detection, description hashing, term
   extraction, duration extraction, season mapping, citizenship/sponsorship
   classification, mechanical classifier, fit-score calculation, and source
   failure handling.
2. The SPEC's term/duration example phrases SHALL be explicit test cases.

### Requirement 2.13 — Accuracy & validation
Acceptance criteria:
1. Validation SHALL enforce: active jobs have a valid application URL; all jobs
   have a source URL; source job id present where available; removed jobs retain
   history; every sponsorship conclusion has evidence; every term conclusion has
   evidence or is unknown.
2. An "Accuracy Report" (CLI `health` and/or dashboard) SHALL list jobs with
   missing application URL, unknown term, unknown sponsorship, stale sources, and
   removed/active mismatches.

### Requirement 2.14 — Resilience
Acceptance criteria:
1. The system SHALL continue operating when one company source fails
   (per-company try/except; one failure does not abort the run).
2. Transient HTTP failures SHALL be retried with exponential backoff; permanent
   failures SHALL be recorded in source health.

---

## 3. Deferred (NOT in this spec, but must not be blocked)

- Authentication / multi-user login (Phase 2) — DB has nullable `user_id`
  ownership columns / separate tables scaffolded so adding auth later is additive.
- AI relevance analysis + provider abstraction (Phase 3) — `ai_analysis` column
  and cache-key fields (`description_hash`, `profile_version`) exist in schema.
- Export for external AI deeper formatting (Phase 3) — basic export done in
  Phase 1.
- Resume studio (Phase 4).
- Discord notifications, GitHub Actions, scheduler, hosted DB (Phase 5) —
  notification history table MAY be scaffolded; sending deferred.
