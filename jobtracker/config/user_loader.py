"""Load and validate ``config/user.yaml`` into :class:`UserProfile`."""

from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import ValidationError

from jobtracker.config.companies_loader import ConfigError
from jobtracker.schemas.user_profile import UserProfile


def load_user_profile(path: str | Path) -> UserProfile:
    """Parse the user YAML and return a validated :class:`UserProfile`.

    The file must contain a top-level ``user:`` mapping.
    """
    file_path = Path(path)
    if not file_path.exists():
        raise ConfigError(
            f"User config not found at '{file_path}'. "
            "Create it or set user_file in settings/.env."
        )
    try:
        with file_path.open("r", encoding="utf-8") as handle:
            data = yaml.safe_load(handle)
    except yaml.YAMLError as exc:
        raise ConfigError(f"Could not parse YAML in '{file_path}': {exc}") from exc

    if not isinstance(data, dict) or "user" not in data:
        raise ConfigError(f"'{file_path}' must contain a top-level 'user:' mapping.")

    user_block = data["user"]
    if not isinstance(user_block, dict):
        raise ConfigError(
            f"'user' in '{file_path}' must be a mapping, got {type(user_block).__name__}."
        )

    try:
        return UserProfile(**user_block)
    except ValidationError as exc:
        raise ConfigError(f"Invalid user profile in '{file_path}':\n{exc}") from exc
