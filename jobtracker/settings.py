"""Application settings loaded from environment / .env via pydantic-settings."""

from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime configuration for the job tracker.

    Values are read from environment variables or a local ``.env`` file.
    No AI/API key is ever required for the app to run.
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Database
    database_url: str = "sqlite:///./jobtracker.db"

    # Logging
    log_level: str = "INFO"
    log_file: str | None = None

    # Config files
    companies_file: str = "config/companies.yaml"
    user_file: str = "config/user.yaml"

    # Notifications (optional, Phase 5)
    discord_webhook_url: str | None = None

    # Email notifications (FEAT-005). ALL optional: when the SMTP credentials
    # below are absent the notifier falls back to a safe no-credential "outbox"
    # mode that writes rendered emails to ``notify_outbox_dir`` and the
    # NotificationLog instead of sending real email (nothing ever crashes or
    # requires creds). Configure these to send real email via any SMTP provider
    # (Gmail, Resend, Brevo, ...). For Gmail use an *App Password* (not your
    # account password) with smtp_host=smtp.gmail.com, smtp_port=587, TLS on.
    smtp_host: str | None = None
    smtp_port: int = 587
    smtp_username: str | None = None
    smtp_password: str | None = None
    smtp_use_tls: bool = True
    notify_from_email: str | None = None
    notify_default_to_email: str | None = None
    #: Directory the outbox backend writes rendered emails to (no creds needed).
    notify_outbox_dir: str = "output/outbox"
    #: When True the scheduler invokes the notifier after each poll so new jobs
    #: trigger prompt emails. Safe default OFF so plain CLI users are unchanged.
    notify_on_poll: bool = False
    #: Look-back window (minutes) for the post-poll notifier. Scopes the scan to
    #: just-detected changes so work stays proportional to the poll; kept wider
    #: than the max poll interval so nothing in the last cycle is missed (dedup
    #: via NotificationLog makes the overlap safe).
    notify_poll_window_minutes: int = 180

    # Gemini AI (optional, Phase 3). Read from the GEMINI_API_KEY environment
    # variable (or .env). AI is enabled whenever this is a non-empty string; the
    # key format is deliberately NOT validated (Google AI Studio issues
    # "AQ."-prefixed keys). With no key the AI features degrade gracefully to an
    # "add a Gemini API key" message. Never hardcode or commit the key.
    gemini_api_key: str | None = None
    #: Gemini model used for analysis (env: GEMINI_MODEL). Measured against a
    #: real AI Studio free-tier key: gemini-2.5-flash returns 404 (no longer
    #: offered to new users) and the standard flash models allow only 20
    #: requests/day, too few for a few hundred jobs. gemini-3.5-flash-lite
    #: answers in under a second and gave the same eligible/not verdict as
    #: gemini-3.5-flash on every job tested, so it is the default.
    gemini_model: str = "gemini-3.5-flash-lite"
    #: Seconds to sleep BETWEEN provider calls in the bulk AI pass, to stay
    #: under the Gemini free-tier per-minute request limit. Tests pass
    #: delay_seconds=0.
    ai_request_delay_seconds: float = 4.0
    #: Cap on the number of NEW analyses performed per bulk run (None =
    #: unlimited). With a cap, a first full backfill of the Stage-1 survivor set
    #: spreads across multiple runs so each run stays within the rate limit.
    ai_bulk_max_per_run: int | None = None
    #: When True the scheduler runs the bulk AI pass over each company's NEW
    #: Stage-1 survivors after polling + screening. Safe default OFF so plain
    #: CLI users (and no-key deployments) see no behaviour change.
    ai_analyze_on_poll: bool = False
    #: Jobs assessed per Gemini request in the bulk pass. The free tier limits
    #: REQUESTS per day (~250), not jobs, so batching many jobs into one request
    #: is what lets the whole survivor set be screened within the quota. 1
    #: restores the old one-job-per-request behaviour. ~12 keeps each prompt
    #: well within the token budget with truncated per-job descriptions.
    ai_batch_size: int = 12

    # Apify (optional third-party fetcher for JS-locked Workday/ATS boards).
    # KEY-READY: with no token the Apify path is simply skipped (those companies
    # stay as stubs); nothing breaks. Read from APIFY_TOKEN. The user's account
    # is free-tier only and must NEVER be billed, so usage is HARD-CAPPED per
    # day (see below) and only FREE Actors are used by default.
    apify_token: str | None = None
    #: Apify Actor used to fetch a Workday board (username~actor-name form).
    #: Defaults to a FREE Actor so the free-tier/no-pay constraint holds.
    apify_workday_actor: str = "gooyer.co~myworkdayjobs"
    #: The free plan renews this many USD of usage credit each month. Used to
    #: derive the per-day cap (monthly budget / days-in-month).
    apify_monthly_budget_usd: float = 5.0
    #: Hard safety cap on what a SINGLE Actor run may be charged. Belt-and-
    #: suspenders with the free-Actor default so a paid run can never surprise-
    #: bill the user. 0.0 = never allow a charge (free Actors only).
    apify_max_run_charge_usd: float = 0.0
    #: Max seconds to BLOCK waiting for a single Apify Actor run before moving
    #: on (the run is retried next poll). Keeps the poll step from stalling on
    #: slow third-party fetches. Kept well under the per-poll budget.
    apify_run_wait_secs: int = 60
    #: Max number of apify_workday companies polled PER run, so a backlog of
    #: slow third-party fetches is spread across polls instead of stalling one.
    apify_max_companies_per_run: int = 8

    # HTTP / connectors
    http_timeout: float = 30.0
    http_max_retries: int = 3
    rate_limit_per_host: float = 2.0

    # Scheduler (FEAT-003). Boards do not push to us, so we poll as fast as is
    # safe and surface accurate timestamps. Defaults are SAFE: the scheduler is
    # OFF unless explicitly enabled, so plain CLI users see no behaviour change.
    scheduler_enabled: bool = False
    #: Hard ceiling applied to every company's effective interval so NO company
    #: ever waits longer than ~1 hour between polls.
    scheduler_max_interval_minutes: int = 60
    #: Per-priority default polling intervals (minutes), used when a company has
    #: no explicit ``check_interval_minutes``. Each is still capped at
    #: ``scheduler_max_interval_minutes``.
    scheduler_high_interval_minutes: int = 15
    scheduler_medium_interval_minutes: int = 45
    scheduler_low_interval_minutes: int = 60


def get_settings() -> Settings:
    """Return a freshly loaded settings instance."""
    return Settings()
