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

    # AI provider (optional, unused in Phase 1)
    ai_provider: str | None = None
    ai_api_key: str | None = None

    # HTTP / connectors
    http_timeout: float = 30.0
    http_max_retries: int = 3
    rate_limit_per_host: float = 2.0


def get_settings() -> Settings:
    """Return a freshly loaded settings instance."""
    return Settings()
