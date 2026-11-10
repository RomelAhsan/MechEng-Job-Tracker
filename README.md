# MechEng-Job-Tracker

> ## 🌐 Live site: https://RomelAhsan.github.io/MechEng-Job-Tracker/
> A free, always-on React page of the co-op/internship jobs you're eligible
> for, refreshed after every collection run (~15 min). No PC, no sign-in to
> browse. Turn it on once via **Settings → Pages → Source = GitHub Actions**.
> It's read-only (browse/filter/apply); accounts, saved jobs, and the email
> opt-in live in the full Streamlit app.
>
> ### 👉 New here? Read **[START_HERE.md](START_HERE.md)** instead.
> It is a four-step, browser-only setup (no terminal) that gets job alerts
> emailed to your Gmail every 2 hours, collected on GitHub's servers so your own
> computer does not need to be on. This README is the full technical reference.

A local-first tracker for mechanical-engineering co-op / internship postings. It
aggregates company career pages (ATS boards) into one searchable SQLite database,
applies deterministic, evidence-first screening for a University of Waterloo
Mechanical Engineering student, and surfaces everything through a Streamlit
dashboard.

This repository implements the project **end to end (SPEC Phases 0–5)**:
connectors for many public ATS boards, a background polling scheduler with a
`<=1h` freshness guarantee, user accounts + personalization, email
notifications with per-filter/per-page subscriptions, a key-ready Gemini AI
relevance engine (with a graceful no-key fallback), a Resume Studio with strict
fact preservation, add-company-by-name, and a Fly.io deployment path with CI.

> **"The link didn't work" — why, and how to actually open it.** The dashboard
> is a **Streamlit app**. By default it runs **only on your own machine** at
> <http://localhost:8501> (there is no public server unless *you* deploy it).
> Run `python -m jobtracker dashboard` and open that localhost URL. To get a
> real public `https://…` URL, deploy to your own Fly.io account — see
> [DEPLOY.md](DEPLOY.md).

## Requirements

- Python 3.11+

## Install

From the repository root:

```bash
pip install -e ".[dev]"
```

Copy `.env.example` to `.env` to override defaults (database URL, log level,
HTTP settings, etc.). **No AI/API key is required to run anything.**

## Quick start (local, one command at a time)

```bash
python -m jobtracker init-db                 # create/upgrade the schema
python -m jobtracker run                     # ingest enabled companies (one-shot)
python -m jobtracker screen                  # classify/extract/score stored jobs
python -m jobtracker dashboard               # open http://localhost:8501
```

Leave the last command running and open **<http://localhost:8501>** in your
browser. For continuously fresh data, run the background worker in a second
terminal:

```bash
python -m jobtracker scheduler               # polls each board on its interval (<=1h)
```

This is a **local-only** app: the dashboard URL above is served by Streamlit on
your machine, not by any hosted server. See [DEPLOY.md](DEPLOY.md) for both the
reliable localhost path and the Fly.io live-URL path.

## Keeping jobs fresh: the scheduler

ATS job boards (Greenhouse, Lever, Workday, …) **do not push** new postings to
us — there is no webhook or feed that notifies us the instant a job goes live.
The only way to discover a new posting is to **poll** the board and compare it
to what we saw last time. True "instant" delivery is therefore impossible; what
we can do is poll as fast as is safe and show accurate timestamps so you always
know how fresh the data is.

`python -m jobtracker scheduler` runs a foreground worker (an APScheduler
`BlockingScheduler`, suitable for a dedicated Fly.io `worker` process) that:

1. Does an **immediate first poll** of every enabled + active company on
   startup, so the dashboard is populated promptly.
2. Then polls each company on its **effective interval**, computed as:

   ```
   effective_interval = min(
       company.check_interval_minutes OR the priority default,
       scheduler_max_interval_minutes,   # default 60
   )
   ```

   Priority defaults are **high = 15 min, medium = 45 min, low = 60 min**, and
   every value is capped at `scheduler_max_interval_minutes` (default **60**),
   so **no company ever waits longer than ~1 hour** between polls. A company can
   set an explicit `check_interval_minutes` to poll faster; it is still capped
   at the global maximum.

Each poll is **fail-soft per company**: one source failing never stops the
scheduler, and the failure is recorded against `source_health`. New/changed
jobs are screened automatically as part of each poll.

The scheduler is **off by default** (`scheduler_enabled=False`) so plain CLI
users see no behaviour change; the one-shot `run` command is unchanged. The
scheduling decisions are pure functions (`compute_interval`, `build_jobs` in
`jobtracker/scheduler/runner.py`) so they are unit-tested without real timers,
threads, or network.

### Seeing freshness on the dashboard

Because we poll rather than receive pushes, the dashboard makes timing
transparent instead of pretending it is instant:

- Each job card shows **Posted**, **First seen** (when *we* first observed it,
  relative + absolute), and **Source last checked**.
- The **Source Health** page shows, per source, `last_checked_at`,
  `last_successful_check_at`, `last_change_detected_at`, new/removed counts for
  the last run, the current job count, and the last error.
- A **Refresh** button re-queries after a poll so new jobs appear promptly.

Relevant settings (see `jobtracker/settings.py`, all overridable via `.env`):
`scheduler_enabled`, `scheduler_max_interval_minutes`,
`scheduler_high_interval_minutes`, `scheduler_medium_interval_minutes`,
`scheduler_low_interval_minutes`.

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
`streamlit run jobtracker/dashboard/app.py` (locally at
<http://localhost:8501>). The dashboard is gated behind a lightweight login
(see [Accounts & personalization](#accounts--personalization)); every page is a
thin view over the service layer:

1. **Relevant For Me** (default) — your **two-stage eligible set**: a
   deterministic Stage-1 hard filter removes jobs you cannot take (full-time /
   new-grad, advanced-degree or senior-experience requirements, ITAR /
   US-citizen-only barriers, incompatible stated term/duration, removed
   postings), THEN the Stage-2 AI judgment runs over the survivors and hides
   any the model explicitly rules out. What remains is strong + possible
   matches with new-job badges, fit score, concerns, apply + source links,
   freshness timestamps, and filters (term, duration, location, country,
   remote, employment type, sponsorship eligibility, freshness window,
   fit-score range, classification). **With no valid Gemini key the Stage-2
   gate is a no-op**, so the page falls back to exactly the Stage-1 survivors
   and is never empty.
2. **All Internships** — the full active database with advanced filters, a
   "Show removed (archive)" toggle, source info, and direct links.
3. **Companies** — a table with source-health summary, an **add-company-by-name**
   form, an add-by-URL form, and edit / enable / disable / remove plus priority
   / interval controls (all writes go through `company_service`).
4. **Source Health** — last check times, run counts, errors, source status, the
   quarantine queue, and the full accuracy report.
5. **Saved & Pipeline** — per-user saved jobs and application-status tracking.
6. **AI Analysis** — the key-ready Gemini relevance engine (see
   [AI relevance engine](#ai-relevance-engine)).
7. **Resume Studio** — upload a master resume and generate a tailored version
   with strict fact preservation (see [Resume Studio](#resume-studio)).

### ITAR / US-citizen-only filtering

The Phase-1 profile is a **Canadian citizen** who cannot work roles that require
US citizenship for ITAR / export-control compliance. During screening, the
work-authorization extractor (`jobtracker/extract/work_auth.py`) detects
ITAR / export-control / `U.S. Person` / US-citizen-only phrasing (for example
"requires ITAR compliance", "due to export control regulations", "must be a U.S.
citizen", "U.S. citizens or permanent residents only") and records the exact
matched phrase as evidence, resolving the job to the `ineligible_itar`
eligibility status. This barrier takes precedence over any sponsorship-available
phrasing.

Jobs with `ineligible_itar` are **filtered out of every dashboard view
entirely** — not merely flagged. The exclusion is applied centrally in the
read-side `dashboard_service`, so it holds for both *Relevant For Me* and *All
Internships*, including the "Show removed (archive)" view, and it cannot be
defeated by the eligibility-status filter widget (`ineligible_itar` is never a
selectable option). Genuinely ambiguous roles (missing or unclear work-auth
information) remain visible as `possibly_eligible_unknown`; only explicit
ITAR / US-citizen-only barriers are excluded.

### Two-stage eligibility

ITAR filtering is one rule inside a broader **two-stage eligibility** model:

- **Stage 1 — deterministic hard filter.** During screening each job gets a
  persisted verdict (`Job.eligible`). A job that is clearly not a co-op you
  could take — full-time / new-grad, requires an advanced degree or senior
  years of experience, ITAR / US-citizen-only, or states an incompatible
  term/duration — is marked `eligible=False` and **removed from every browsing
  view everywhere** (Relevant For Me, All Internships, the archive, and the
  filter-option lists), exactly like the ITAR exclusion above. Un-screened
  (`NULL`) and surviving (`True`) jobs stay visible.
- **Stage 2 — AI judgment on the survivors.** *Relevant For Me* then intersects
  the Stage-1 survivors with the cached AI verdict: a survivor the model
  explicitly rules out (a `skip` recommendation or a plainly-negative
  work-authorization / duration / term assessment) is hidden; everything else
  is kept.

The Stage-2 gate reads **only the cached AI analysis** (no network at page
load) and is **non-blocking on uncertainty**: a survivor with no cached verdict
is kept. So with no valid Gemini key — or before the bulk AI pass has run —
*Relevant For Me* is exactly the Stage-1 survivors and is **never empty**.

## Configuration

Configuration comes from environment variables / `.env` (see `.env.example`) and
two YAML files:

- `config/companies.yaml` — the tracked employer list. Each entry has `id`,
  `name`, `ats` (`greenhouse`/`lever`/`ashby`/`smartrecruiters`/`workable`/`rss`/
  `sitemap`/`json_ld`/`html`), optional `board_token`, `careers_url`, `enabled`,
  `priority` (`high`/`medium`/`low`), `check_interval_minutes`, `locations`, and
  `notes`. Unsupported ATS types route to a fallback stub (`requires_fallback`).

  The seed list ships **35 companies with connectors verified live** against
  their public ATS endpoint (a 200 returning real postings for that employer),
  so only confirmed tokens/tenants are ever committed — nothing is fabricated.
  These span mechanical / hardware / robotics / aerospace / automotive /
  manufacturing employers, including Greenhouse boards (SpaceX, Relativity
  Space, Rocket Lab, Archer Aviation, Lucid Motors, Astranis, Figure, Nuro,
  Waymo, Kodiak Robotics, Neuralink, Redwood Materials, Divergent, Vast, Ursa
  Major, Antora Energy, Muon Space, Planet Labs, Markforged, ChargePoint), Lever
  boards (Shield AI, Zoox, Dexterity, Bright Machines), Ashby boards (1X
  Technologies, Saronic, Gecko Robotics, Physical Intelligence, Standard Bots),
  and **Workday** boards (NVIDIA, Stryker, Medtronic, KLA, Magna International,
  Enbridge). The Workday connector talks to the public CXS JSON endpoint
  (`{tenant}.{dc}.myworkdayjobs.com/wday/cxs/{tenant}/{site}/jobs`), paginates by
  offset, and fetches each posting's full description + canonical public apply
  URL via the per-posting detail call; each tenant/data-center/site triple was
  probed live before being committed. A Workable connector (public
  `apply.workable.com` widget JSON) and generic RSS / sitemap / JSON-LD
  connectors are also available for employers that publish through those
  channels. The remaining entries are well-known employers (Apple, Tesla,
  Boeing, Lockheed Martin, GM, Ford, and several Canadian firms) whose public
  ATS could not be confirmed on a supported connector — or that are auth-walled
  (e.g. Rivian's Workday board returns HTTP 401, which we never bypass) — so they
  stay as documented `ats: html` fallback stubs (visible in Source Health, no
  fabricated tokens) until a connector is added. ITAR / US-citizen-only roles
  are excluded per posting by the work-authorization filter, so a defense-heavy
  employer is never dropped wholesale — only its individual ITAR-restricted
  roles are hidden.
- `config/user.yaml` — the single implicit Phase-1 user profile (citizenship,
  work authorization, available terms, preferred duration, interests, skills).

Key settings (`jobtracker/settings.py`, all overridable via `.env` — see
`.env.example` for the full list):

- **Core:** `database_url` (default `sqlite:///./jobtracker.db`), `log_level`,
  `log_file`, `companies_file`, `user_file`.
- **HTTP/connectors:** `http_timeout`, `http_max_retries`, `rate_limit_per_host`.
- **Scheduler:** `scheduler_enabled`, `scheduler_max_interval_minutes` (default
  60), `scheduler_high/medium/low_interval_minutes`.
- **Email:** `smtp_host`, `smtp_port`, `smtp_username`, `smtp_password`,
  `smtp_use_tls`, `notify_from_email`, `notify_default_to_email`,
  `notify_outbox_dir`, `notify_on_poll`.
- **AI (optional):** `gemini_api_key` (env `GEMINI_API_KEY`), `gemini_model`
  (env `GEMINI_MODEL`, default `gemini-3.5-flash-lite`).

## Accounts & personalization

The dashboard requires a login. The **first** account to register becomes the
`admin` (the owner of the shared company registry — only an admin can add / edit
/ remove companies); everyone after is a `member`. Passwords are bcrypt-hashed
and never stored or logged. Each user has their own saved jobs, application
pipeline, saved filters/subscriptions, and resume studio data. Personalization
(interests, skills, available terms, preferred duration) feeds the fit score and
the AI prompt; editing a profile bumps its `profile_version`, which invalidates
that user's cached AI analyses.

## AI relevance engine

The app is **key-ready** for Google's Gemini. With **no key configured** the AI
Analysis page and the `analyze` command degrade gracefully with an
"add a Gemini API key" message and never crash — every other feature works
without AI.

To enable live analysis:

1. Create a free key at **Google AI Studio** (<https://aistudio.google.com/apikey>).
   AI Studio keys start with `AQ.`; the app accepts any non-empty key and does
   not check its format.
2. Set `GEMINI_API_KEY=<your key>` in your `.env` (or as a GitHub Actions / Fly
   secret). Optionally set `GEMINI_MODEL` (default `gemini-3.5-flash-lite`).
3. Check the key works: `python scripts/test_gemini.py` makes one call and
   prints the reply.

Calls go through Google's official `google-genai` SDK to the native Gemini
endpoint. The key is read only from the environment / gitignored `.env` and is
never committed.

**Free-tier limits (measured):** `gemini-2.5-flash` returns 404 for new AI
Studio users, and the standard flash models allow only 20 requests per day.
`gemini-3.5-flash-lite` answers in under a second and matched
`gemini-3.5-flash`'s eligible/not-eligible verdict on every job tested, so it
is the default. If the daily quota still runs out, the bulk pass stops cleanly
and resumes on the next run; jobs not yet analyzed stay on your page using the
Stage-1 result.

Analyses are cached by `(job_id, description_hash, profile_version)` so repeated
views are free; low-confidence results are surfaced on the Source Health
accuracy report for a human to double-check.

## Resume Studio

Upload your master resume (PDF/DOCX/text) once; the Resume Studio reorders and
emphasizes the content that is **already there** to match a target job. It
**never invents** experience, skills, dates, employers, schools, tools, or
metrics: a deterministic `verify_claims` pass strips any generated line not
grounded in your master resume (SPEC CORE RULE #7/#8), and the page reports a
change log plus a "missing keywords" list (suggestions only, never inserted).
Export the tailored resume to PDF/Markdown/text.

## CLI commands

| Command | Description |
| --- | --- |
| `init-db` | Create or upgrade the database to the latest Alembic revision (idempotent). |
| `add-company URL [--id --name --priority]` | Detect a company's ATS from a careers URL, save it, and baseline-import. |
| `add-company-by-name NAME [--id --priority]` | Resolve a plain company name to a public board and baseline-import (see below). |
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
| `scheduler` | Run the foreground polling worker (immediate first poll, then interval polling capped at ~1h). |
| `notify [--since-hours N]` | Email users about recent new/changed/removed jobs and source failures (deduped). |
| `digest [--since-hours N]` | Email each subscribed user a periodic summary of recent matching jobs. |
| `analyze --job ID [--force]` | Run the Gemini AI relevance engine on one job (safe with no key). |
| `backup --output PATH` | Back up the SQLite database (safe online copy). |
| `restore --input PATH` | Restore the SQLite database from a backup. |

Run `python -m jobtracker --help` for the full surface.

### Add a company by name

```bash
python -m jobtracker add-company-by-name "Toyota"
```

This probes the known public board patterns for the name's slug
(`boards.greenhouse.io/{slug}`, `jobs.lever.co/{slug}`, `jobs.ashbyhq.com/{slug}`,
`apply.workable.com/{slug}`), picks the **first that returns real postings**, and
baseline-imports it (existing jobs are marked *seen*, so you never get a flood of
false "new" notifications). If the name cannot be auto-resolved — common for
large Workday-based employers whose URL needs a verified tenant/site that cannot
be guessed — it prints a helpful message telling you to use `add-company` with
the careers URL instead. The same flow is available on the **Companies** page in
the dashboard.

### Backup & restore

The database is a single SQLite file, so backups are a safe file copy (via
SQLite's online backup API, so `backup` is safe even while the app is running):

```bash
python -m jobtracker backup --output ./backups/jobtracker.db
python -m jobtracker restore --input ./backups/jobtracker.db   # stop the app first
```

On Fly.io you can also use volume snapshots; see [DEPLOY.md](DEPLOY.md).

### Email notifications

Email is **OPT-IN and EXACT-MATCH only**. By default **no account receives any
email**. You opt in on the **Notifications** page by ticking **"Enable email
notifications"** (and optionally **"Email me jobs on my page"** to subscribe to
your current Relevant-For-Me filters). Once opted in, `notify` and `digest`
email you **only about jobs in your exact-match "Relevant For Me" set** — the
same final eligible set (Stage-1 survivors INTERSECT the AI-eligible gate). The
old broad rules (strong-match, high-priority possible, spring four-month,
preferred-location, US-sponsorship) **no longer trigger emails on their own**: a
job is emailed only if it is in that eligible set, and even saved-filter
subscriptions are intersected with it so a non-eligible / non-co-op job can
never be emailed. Every email lists the direct application URL plus company,
title, location, term, duration, fit score, and reason, and no job is ever
emailed twice for the same rule (dedup via `notification_log`).
`removed_after_saved` and `source_failure` alerts are preserved (still gated by
the opt-in).

Delivery is provider-agnostic SMTP. **With no SMTP credentials configured the
notifier runs in a safe "outbox" mode**: rendered emails are written to
`NOTIFY_OUTBOX_DIR` (default `output/outbox/`) and recorded in `notification_log`
instead of being sent — nothing crashes and no credentials are required. To send
real email, set `SMTP_HOST`/`SMTP_PORT`/`SMTP_USERNAME`/`SMTP_PASSWORD` (see
`.env.example`). For Gmail use an **App Password** (not your account password)
with `smtp.gmail.com:587` and TLS on. Enable `NOTIFY_ON_POLL=true` to have the
scheduler email new matches right after each poll.

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
- **A company shows `requires_fallback`** — its ATS is not one of the supported
  connectors yet; it is tracked but not fetched until a connector exists
  (expected for `ats: html` entries).
- **`add-company-by-name` says it couldn't resolve a company** — the employer
  likely uses a Workday board (or a non-public ATS) whose URL can't be guessed;
  use `add-company` with the careers URL from the company's careers page.
- **AI Analysis shows "add a Gemini API key"** — set `GEMINI_API_KEY` in `.env`
  and run `python scripts/test_gemini.py`; see
  [AI relevance engine](#ai-relevance-engine).
- **The dashboard shows empty pages** — point `DATABASE_URL` at a database that
  has been through `init-db` + `run` + `screen`, or seed data first.
- **`streamlit` command not found** — ensure the virtual environment is active;
  Streamlit is installed as a runtime dependency.

## Deployment

- **Local** (recommended for a single user): the Quick start above runs
  everything on your machine; the dashboard is at <http://localhost:8501>.
- **Fly.io** (a real public HTTPS URL): deploy to your own Fly.io account with
  the root `Dockerfile` + `fly.toml`. It runs two processes — a **web** process
  (Streamlit bound to `0.0.0.0:8080`) and a **worker** process (the polling
  scheduler) — with a persistent volume for the SQLite database and a
  release-command that applies migrations. There is **no hosted URL until you
  deploy it yourself**. Full step-by-step (install flyctl, create the volume,
  set secrets, deploy, scale the worker) is in **[DEPLOY.md](DEPLOY.md)**.

Continuous integration runs ruff + the full pytest suite on every push/PR via
`.github/workflows/ci.yml` (no live network).

## Status

All SPEC phases (0–5) are implemented: scaffold, schema/migrations, connectors,
normalization, detection, deterministic screening, export, source health, the
Streamlit dashboard, the polling scheduler, user accounts + personalization,
email notifications, the key-ready AI relevance engine, Resume Studio,
add-company-by-name, and the Fly.io deployment path with CI.
