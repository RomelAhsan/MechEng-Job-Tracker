# Technical Design — Phase 0 & Phase 1

Covers the scaffold (Phase 0) and core ingestion + Streamlit dashboard
(Phase 1). Aligns with `JobAggregateSPEC.md`. Out-of-scope features (auth, AI,
resume, notifications, deployment) are only accommodated structurally.

---

## 1. Overview & guiding principles

- **Backend-first, frontend-replaceable.** All logic lives in the `jobtracker`
  package as services and pure functions. Streamlit (and a future FastAPI/React
  layer) are thin callers of the service API. No business logic in UI code.
- **Deterministic before AI.** Phase 1 is 100% rule-based; AI hooks are only
  schema columns and interfaces.
- **PostgreSQL-compatible SQLite.** Portable SQLAlchemy 2.0 typed models +
  Alembic. JSON columns use SQLAlchemy's portable `JSON` type.
- **Fail-soft ingestion.** Per-company isolation; one source failure never
  aborts a run; everything observable via source-health and a quarantine queue.
- **Evidence everywhere.** Term, sponsorship, and classification conclusions
  carry the exact matched text; never guess (unknown is a first-class value).

---

## 2. Repository / package layout

```text
MechEng-Job-Tracker/
├── pyproject.toml
├── .env.example
├── README.md
├── JobAggregateSPEC.md
├── alembic.ini
├── config/
│   ├── companies.yaml
│   └── user.yaml
├── migrations/                      # Alembic env + versions
│   ├── env.py
│   └── versions/
├── jobtracker/
│   ├── __init__.py
│   ├── __main__.py                  # enables `python -m jobtracker`
│   ├── cli.py                       # Typer app (all SPEC commands)
│   ├── settings.py                  # Pydantic-settings (env/.env)
│   ├── logging_setup.py
│   ├── config/
│   │   ├── companies_loader.py      # parse/validate companies.yaml
│   │   └── user_loader.py           # parse/validate user.yaml
│   ├── schemas/                     # Pydantic v2 models (canonical job, etc.)
│   │   ├── job.py                   # CanonicalJob, enums
│   │   ├── company.py
│   │   └── user_profile.py
│   ├── db/
│   │   ├── base.py                  # DeclarativeBase, engine/session factory
│   │   ├── models.py                # ORM: Company, Job, JobEvent, SourceHealth,
│   │   │                            #      QuarantineItem, NotificationLog(stub),
│   │   │                            #      (User/SavedJob scaffolds, Phase 2)
│   │   └── repositories.py          # data-access helpers
│   ├── connectors/
│   │   ├── base.py                  # Connector ABC + RawJob dataclass + retry
│   │   ├── http.py                  # shared httpx client, backoff, rate limit
│   │   ├── greenhouse.py
│   │   ├── lever.py
│   │   ├── ashby.py
│   │   ├── smartrecruiters.py
│   │   ├── fallback.py              # workable/rss/sitemap/json_ld/html stub
│   │   └── registry.py              # ats -> connector mapping + URL detection
│   ├── normalize/
│   │   ├── normalizer.py            # RawJob -> CanonicalJob
│   │   ├── hashing.py               # description_hash
│   │   └── html_clean.py            # BeautifulSoup/lxml cleanup
│   ├── detect/
│   │   └── diff.py                  # new/changed/removed/active/expired
│   ├── extract/
│   │   ├── term.py                  # term/duration extraction + season map
│   │   └── work_auth.py             # citizenship/sponsorship classifier
│   ├── screen/
│   │   ├── meche_classifier.py      # mechanical relevance rules + signals
│   │   ├── signals.py               # signal word/phrase lists from SPEC
│   │   └── fit_score.py             # 0-100 weighted score + reasons
│   ├── services/
│   │   ├── ingest_service.py        # orchestrates a run for 1..N companies
│   │   ├── company_service.py       # add/edit/enable/disable/remove + sync
│   │   ├── screening_service.py     # run classify+extract+score over jobs
│   │   ├── export_service.py        # markdown/json/csv export
│   │   └── health_service.py        # source health + accuracy report
│   ├── dashboard/
│   │   ├── app.py                   # Streamlit entry
│   │   └── pages/                   # Relevant / All / Companies / Source Health
│   └── notifications/               # (Phase 5) stub module only
├── tests/
│   ├── conftest.py
│   ├── fixtures/                    # sample ATS JSON + expected normalized
│   └── test_*.py
└── output/                          # export outputs (gitignored)
```

Rationale: each pipeline stage from the SPEC architecture diagram maps to a
package (`connectors → normalize → detect → screen/extract → db → dashboard`).

---

## 3. Data model (SQLAlchemy 2.0, portable)

Portable column types only: `Mapped[...]` with `String`, `Text`, `Integer`,
`Float`, `Boolean`, `DateTime(timezone=True)`, `JSON`. Timestamps stored UTC.

### 3.1 `companies`
| column | type | notes |
|---|---|---|
| id (PK) | String | slug e.g. `acme` |
| name | String | |
| ats | String | enum-validated in app layer |
| board_token | String | nullable |
| careers_url | String | |
| enabled | Boolean | default true |
| active | Boolean | false when "removed" (history kept) |
| priority | String | high/medium/low |
| check_interval_minutes | Integer | nullable → derived from priority |
| locations | JSON | list[str] |
| notes | Text | nullable |
| created_at / updated_at | DateTime | |

### 3.2 `jobs` (canonical schema)
PK `job_id` (String UUID). Unique constraint `(company_id, source_job_id)`.
Columns mirror the SPEC canonical schema exactly, including: title,
normalized_title, location, city, province_state, country, remote_status,
employment_type, department, team, description (Text), requirements (JSON),
preferred_qualifications (JSON), skills (JSON), description_hash,
application_url, source_url, source_type, posted_at, updated_at, first_seen_at,
last_seen_at, removed_at, status, term_season, term_start_date, term_end_date,
duration_months, duration_label, citizenship_requirement,
work_authorization_requirement, visa_sponsorship, sponsorship_confidence,
sponsorship_evidence, classification, fit_score, classification_confidence,
classification_reasons (JSON), ai_analysis (JSON, null in Phase 1),
raw_payload (JSON, for debugging), eligibility_status (String — stores values
like `possibly_eligible_unknown`, `likely_ineligible_sponsorship_required`).

Indexes: `company_id`, `status`, `classification`, `term_season`, `posted_at`,
`first_seen_at`.

### 3.3 `job_events`
Append-only audit of detection: `id`, `job_id`, `company_id`, `event_type`
(`new|changed|removed|reactivated`), `old_hash`, `new_hash`, `created_at`.
Backs the dashboard "new badge", freshness, and accuracy report.

### 3.4 `source_health`
One row per company: `company_id` (PK/FK), `last_checked_at`,
`last_successful_check_at`, `last_change_detected_at`, `current_job_count`,
`new_jobs_last_run`, `removed_jobs_last_run`, `error_count`, `last_error`,
`last_status` (`ok|error|requires_fallback`).

### 3.5 `quarantine_items`
Jobs/payloads that failed validation (missing application_url, parse error):
`id`, `company_id`, `source_job_id`, `reason`, `raw_payload` (JSON),
`created_at`, `resolved` (Boolean).

### 3.6 Scaffolds (created now, unused until later phases)
- `users`, `saved_jobs`, `application_status`, `user_profiles` with
  `profile_version` — minimal nullable tables so Phase 2 is additive. In
  Phase 1 a single implicit user from `config/user.yaml` is used; schema holds a
  `profile_version` integer the AI cache key will consume in Phase 3.
- `notification_log` — created empty for Phase 5.

### 3.7 Migrations
Single initial Alembic revision `0001_initial` creating all of the above.
`init-db` runs `alembic upgrade head` programmatically. Alembic `env.py` reads
the DB URL from `settings`.

---

## 4. Configuration

- `settings.py` — Pydantic-settings: `database_url`
  (default `sqlite:///./jobtracker.db`), `log_level`, `log_file`,
  `companies_file`, `user_file`, `discord_webhook_url` (optional),
  `ai_provider`/`ai_api_key` (optional, unused Phase 1), `http_timeout`,
  `http_max_retries`, `rate_limit_per_host`.
- `config/companies.yaml` — seeded with a handful of example engineering
  companies (toward the ~25 target; user edits/extends). Loader validates with a
  Pydantic `CompanyConfig` model and reports actionable errors.
- `config/user.yaml` — the SPEC user profile block; validated by
  `UserProfile` Pydantic model. `profile_version` tracked in DB.

Companies sync strategy: `companies.yaml` is the declarative seed. `company_service.sync_from_yaml()`
upserts by `id`. Dashboard/CLI edits write to the DB and can optionally be
exported back to YAML (export-to-YAML is a nice-to-have; DB is authoritative at
runtime).

---

## 5. Connectors

### 5.1 Contract
```python
class RawJob(TypedDict-like dataclass): source_job_id, title, location,
    description_html, application_url, source_url, posted_at, updated_at,
    department, team, employment_type_hint, extra: dict

class Connector(ABC):
    ats: str
    def fetch(self, company: CompanyConfig) -> list[RawJob]: ...
```
Shared `http.py` provides an `httpx.Client` with timeout, a token-bucket-ish
per-host rate limiter, and `retry(fn)` doing exponential backoff on
429/5xx/transport errors (configurable max retries). Connectors never follow
auth walls.

### 5.2 Per-connector mapping
- **Greenhouse** `GET boards-api.greenhouse.io/v1/boards/{token}/jobs?content=true`
  → `jobs[]`: `id`, `title`, `location.name`, `content` (HTML, encoded),
  `absolute_url` (application + source url), `updated_at`. No pagination needed
  (returns all); still coded defensively.
- **Lever** `GET api.lever.co/v0/postings/{token}` (add `?mode=json`) →
  list: `id`, `text` (title), `categories.location`, `descriptionPlain`/
  `description`, `hostedUrl`/`applyUrl`, `createdAt`.
- **Ashby** `GET api.ashbyhq.com/posting-api/job-board/{token}` → `jobs[]`:
  `id`, `title`, `location`, `descriptionHtml`, `jobUrl`/`applyUrl`,
  `publishedDate`, `isListed`.
- **SmartRecruiters** `GET api.smartrecruiters.com/v1/companies/{id}/postings`
  → paginated (`offset`/`limit`, `totalFound`); each posting then normalized
  (`id`, `name`, `location`, `ref`/apply URL). Detail fetch for description only
  if needed and cheap.
- **Fallback** (`workable/rss/sitemap/json_ld/html`): returns no jobs, sets
  source-health `last_status = requires_fallback`, logs a clear message. Keeps
  the pipeline alive; full implementation deferred.

`registry.py` maps `ats → Connector` and provides `detect_ats(url)` for
`add-company` (greenhouse/lever/ashby/smartrecruiters domain patterns →
ats + extracted token).

---

## 6. Normalization & hashing

- `normalizer.normalize(raw, company) -> CanonicalJob`:
  - Clean HTML → plain text via `html_clean` (BeautifulSoup + lxml), strip
    scripts/styles, collapse whitespace.
  - Parse location into city/province_state/country with a lightweight
    heuristic + known Canada/US tokens (Ontario, ON, Canada, US states).
  - Infer `remote_status` and `employment_type` from title/description keywords
    (co-op/internship/intern/new grad), defaulting to `unknown`.
  - Split requirements / preferred qualifications / skills using section
    heuristics + the SPEC skill signal list for skill extraction.
- `hashing.description_hash(job)`: SHA-256 over normalized
  `description + title + location + requirements` → stable hex. Used for
  change detection and (later) AI cache key.

Validation gate: if `application_url` or `source_url` missing on an otherwise
active job → send to quarantine (not dropped).

---

## 7. Detection (`detect/diff.py`)

Given current fetched jobs for a company and stored jobs:
1. Key by `(company_id, source_job_id)`.
2. Not in DB → `new` (unless baseline mode → insert as seen, no event, no
   notification flag).
3. In DB, hash changed → `changed`; update fields + emit `changed` event.
4. In DB, hash same → `active`; bump `last_seen_at`.
5. In DB & active, absent from current fetch → `removed`; set `removed_at`,
   `status=removed`, emit `removed` event. Reappearing later → `reactivated`.
6. `expired` computed when a parsed deadline < now.
7. Update `source_health` counts for the run.

Baseline import (first add / `add-company`) runs the same diff but suppresses
`new` events and notification flags so existing postings aren't false-new.

---

## 8. Extraction

### 8.1 Term & duration (`extract/term.py`)
Deterministic regex/keyword pipeline:
- Detect explicit season words (Spring/Summer/Fall/Winter) + year.
- Month-range patterns ("May-August", "September to December",
  "beginning May 2027") → map to season via the SPEC table.
- Duration patterns ("4-month", "12-month", "4 months") → `duration_months`
  + `duration_label`; derive end date from start + duration when possible.
- Season→dates per SPEC: Spring/Summer May 1–Aug 31, Fall Sep 1–Dec 31,
  Winter Jan 1–Apr 30.
- No match → `term_season = unknown`, flagged "needs term info". Never guess.
- Record the matched substring as evidence.
All SPEC example phrases become unit tests.

### 8.2 Citizenship & sponsorship (`extract/work_auth.py`)
Keyword/phrase matching producing `citizenship_requirement`,
`work_authorization_requirement`, `visa_sponsorship`, `sponsorship_confidence`
(0–1 by match strength), `sponsorship_evidence` (exact quote), and derived
`eligibility_status` for the Canadian-citizen profile per the SPEC decision
rules (Canadian eligible unless foreign PR required; US eligible only if
sponsorship provided / CA auth accepted; US "no sponsorship" →
`likely_ineligible_sponsorship_required`; missing → `possibly_eligible_unknown`).

---

## 9. Screening & fit score (`screen/`)

- `meche_classifier.classify(job)` → `(classification, reasons[], confidence)`
  using `signals.py` lists (strong titles, skills, adjacent roles, negative
  fields). Recall-first: adjacent → `possible_match`; `not_relevant` only when
  title AND description are clearly unrelated.
- `fit_score.score(job, profile)` → `(score 0-100, matched[], concerns[],
  missing[], evidence[], confidence)` using SPEC weights. Hard filters
  (removed/expired, incompatible term/duration/location/citizenship) zero out
  "Relevant" eligibility (surfaced as concerns, not hidden from All Internships).
- `screening_service` persists classification/score/extraction fields onto the
  job rows after ingestion (CLI `screen`, and automatically at end of `run`).

---

## 10. Services & orchestration

- `ingest_service.run(company_ids=None, baseline=False)`:
  for each enabled+active company → pick connector → `fetch` (fail-soft) →
  normalize → validate/quarantine → diff/persist → update source_health → then
  `screening_service` over new/changed jobs. Per-company try/except; aggregate
  summary returned and logged.
- `company_service`: `sync_from_yaml`, `add_from_url` (detect→test→save→
  baseline import→mark seen), `enable/disable/remove` (remove = active=False,
  enabled=False, history kept), list.
- `export_service`: markdown/json/csv with the SPEC export fields + user profile
  summary; writes to `output/`.
- `health_service`: source-health view + accuracy report queries
  (missing application URL, unknown term, unknown sponsorship, stale sources,
  removed-but-present / active-but-absent mismatches).

---

## 11. Dashboard (Streamlit, Phase 1 subset)

`dashboard/app.py` uses `st.navigation`/sidebar to the Phase-1 pages, each a thin
view over services/repositories (read-mostly):
1. **Relevant For Me** (default): strong+possible matches, new-job badges,
   fit score, concerns, apply + source links, filters (term, duration, location,
   country, remote, employment type, sponsorship/eligibility, freshness window,
   fit-score range, classification). Save/AI buttons rendered but disabled/stubbed.
2. **All Internships**: full active DB + advanced filters + a "Show removed
   (archive)" toggle + source info + direct links.
3. **Companies**: table + add-by-URL form + edit/enable/disable/remove + source
   health summary + priority/interval controls (writes via `company_service`).
4. **Source Health**: last check times, run counts, errors, API status, and the
   quarantine list; includes the Accuracy Report.

`python -m jobtracker dashboard` shells out to `streamlit run
jobtracker/dashboard/app.py`.

---

## 12. CLI (`cli.py`, Typer)

Implemented Phase 1: `init-db`, `add-company`, `list-companies`,
`enable-company`, `disable-company`, `remove-company`, `run` (+`--company`),
`screen`, `export` (`--format`/`--output`), `dashboard`, `health`,
`test-source`. Registered-but-deferred (clear message): `analyze`, `notify`,
`digest`. `__main__.py` wires `python -m jobtracker`.

---

## 13. Logging, resilience, rate limits

- `logging_setup.configure(settings)` — stdout + optional rotating file, level
  from settings, per-company contextual logging during runs.
- Resilience: per-company isolation, exponential backoff on transient HTTP,
  errors recorded to `source_health` (`error_count`, `last_error`), fallback
  stub for unsupported ATS. One source down ≠ run aborted.
- Rate limiting: shared per-host throttle in `http.py`.

---

## 14. Testing strategy

- `pytest`, `respx`/`pytest-httpx` to mock httpx; **no network** in unit tests.
- `tests/fixtures/` holds sample Greenhouse/Lever/Ashby/SmartRecruiters JSON +
  expected normalized output.
- Coverage per Requirement 2.12: connectors, normalization, hashing,
  new/changed/removed detection, term + duration + season mapping (incl. all
  SPEC example phrases), citizenship/sponsorship classification, mechanical
  classifier, fit score, source-failure handling.
- A dedicated test asserts no connector performs real network I/O (transport
  mocked).

---

## 15. Design decisions / trade-offs

1. **Frontend = Streamlit, logic in services.** Satisfies "replaceable
   frontend"; a future FastAPI layer reuses the same services.
2. **YAML seeds DB; DB authoritative at runtime.** Supports both "edit YAML" and
   "edit dashboard" without a two-way-sync headache (optional export-to-YAML).
3. **Unsupported ATS → fallback stub, not error.** Satisfies "system continues
   if a source fails" and keeps config values valid now, implementation later.
4. **Single implicit user in Phase 1, multi-user tables scaffolded.** Keeps
   Phase 1 runnable while making Phase 2 auth purely additive.
5. **Evidence + `unknown` as first-class.** Directly enforces the SPEC's
   "never guess / never silently hide" accuracy rules.
6. **AI columns present, logic absent.** `ai_analysis`, `description_hash`, and
   `profile_version` exist so Phase 3 caching is a drop-in.

---

## 16. Resolved decisions (confirmed by user)

1. **Streamlit** is the Phase 1 frontend. ✅
2. **FastAPI deferred** to a later phase. Services are structured now and called
   directly by Streamlit; the FastAPI app is additive later. ✅
3. **Company seeding — two steps:** ✅
   - Start `config/companies.yaml` with a **small verified seed** (3–5
     confirmed-public ATS boards) so the pipeline and tests work immediately.
   - Then populate the **full user-provided target list** (below). Each entry's
     `ats` + `board_token`/`company_identifier` MUST be **detected/verified via
     `detect_ats` against the real careers URL**, not guessed. Companies whose
     public ATS cannot be confirmed are added with the detected fallback type
     (e.g. `ats: html`), `enabled: true`, routed to the fallback stub
     (`requires_fallback`) so they never crash the run and are visible in Source
     Health as needing a connector in a later phase. No fabricated board tokens.
   - Target companies (as the user grouped them; Tesla listed once).
     Big Tech / consumer hardware: Apple, Tesla, Microsoft, Google, Meta, NVIDIA,
     Intel, Amazon, Dyson. Aerospace/defense/space: SpaceX, Blue Origin, Lockheed
     Martin, Northrop Grumman, Boeing, RTX (Raytheon), Relativity Space, Rocket
     Lab, Joby Aviation, Archer Aviation, General Dynamics, L3Harris. Automotive/
     EV/heavy machinery: Rivian, Lucid Motors, GM, Ford, Caterpillar, John Deere,
     Cummins. Energy/oil & gas: ExxonMobil, Chevron, Shell, BP, SLB
     (Schlumberger), Halliburton, Baker Hughes, GE Vernova. Hardware/
     semiconductor equipment & medical devices: ASML, Applied Materials, Lam
     Research, KLA, Texas Instruments, Qualcomm, Stryker, Medtronic, Intuitive
     Surgical. Canada: MDA Space, Magna International, Pratt & Whitney Canada,
     Bruce Power, Ontario Power Generation, TC Energy, Enbridge, Imperial Oil,
     Suncor Energy, Linamar, Bombardier, CAE, Sanctuary AI, Clearpath Robotics
     (Rockwell Automation).
   - Priority heuristic for seeding: Canadian employers + strong mechanical/
     robotics/automotive/aerospace firms → `high`; most others → `medium`;
     any requiring fallback → `low` until a connector exists. User can edit.
4. **Full descriptions everywhere.** All connectors (incl. SmartRecruiters)
   fetch the complete job description — issuing per-posting detail calls when the
   list endpoint omits the body — so no career-page context is lost. Raw payloads
   are stored for debugging; rate limiting + backoff keep the extra calls safe. ✅
5. **Phase 1 dashboard pages:** Relevant For Me / All Internships / Companies /
   Source Health. Saved/AI/Resume pages deferred to their phases. ✅
