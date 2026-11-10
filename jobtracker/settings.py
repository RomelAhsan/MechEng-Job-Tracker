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

    # AI provider (optional, Phase 3). The app is KEY-READY: with no key the AI
    # features degrade gracefully (an "add a Gemini API key" message) instead of
    # crashing. Set ai_provider="gemini" and a REAL Google AI Studio key (looks
    # like "AIza...") in ai_api_key to enable live analysis. The key is read
    # only from env/.env and is never committed.
    ai_provider: str | None = None
    ai_api_key: str | None = None
    #: Gemini model used for analysis when a key is configured.
    ai_model: str = "gemini-1.5-flash"
    #: Seconds to sleep BETWEEN provider calls in the bulk AI pass. The Gemini
    #: 1.5 flash free tier allows ~15 requests/minute, so the 4.0s default keeps
    #: a bulk run comfortably under that ceiling. Tests pass delay_seconds=0.
    ai_request_delay_seconds: float = 4.0
    #: Cap on the number of NEW analyses performed per bulk run (None =
    #: unlimited). With a cap, a first full backfill of the Stage-1 survivor set
    #: spreads across multiple runs so each run stays within the rate limit.
    ai_bulk_max_per_run: int | None = None
    #: When True the scheduler runs the bulk AI pass over each company's NEW
    #: Stage-1 survivors after polling + screening. Safe default OFF so plain
    #: CLI users (and no-key deployments) see no behaviour change.
    ai_analyze_on_poll: bool = False

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
