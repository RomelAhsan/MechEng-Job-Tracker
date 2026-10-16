"""Load and validate ``config/companies.yaml`` into :class:`CompanyConfig`.

Raises :class:`ConfigError` with an actionable message when the file is
missing, malformed, or fails schema validation.
"""

from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import ValidationError

from jobtracker.schemas.company import CompanyConfig


class ConfigError(ValueError):
    """Raised when a configuration file cannot be loaded or validated."""


def _read_yaml(path: str | Path) -> object:
    file_path = Path(path)
    if not file_path.exists():
        raise ConfigError(
            f"Companies config not found at '{file_path}'. "
            "Create it or set companies_file in settings/.env."
        )
    try:
        with file_path.open("r", encoding="utf-8") as handle:
            return yaml.safe_load(handle)
    except yaml.YAMLError as exc:
        raise ConfigError(f"Could not parse YAML in '{file_path}': {exc}") from exc


def load_companies(path: str | Path) -> list[CompanyConfig]:
    """Parse companies YAML and return validated :class:`CompanyConfig` list.

    The file must contain a top-level ``companies:`` list.
    """
    data = _read_yaml(path)
    if not isinstance(data, dict) or "companies" not in data:
        raise ConfigError(
            f"'{path}' must contain a top-level 'companies:' list."
        )

    raw_companies = data["companies"]
    if not isinstance(raw_companies, list):
        raise ConfigError(
            f"'companies' in '{path}' must be a list, got {type(raw_companies).__name__}."
        )

    companies: list[CompanyConfig] = []
    seen_ids: set[str] = set()
    for index, entry in enumerate(raw_companies):
        if not isinstance(entry, dict):
            raise ConfigError(
                f"companies[{index}] in '{path}' must be a mapping, "
                f"got {type(entry).__name__}."
            )
        try:
            company = CompanyConfig(**entry)
        except ValidationError as exc:
            label = entry.get("id") or entry.get("name") or f"index {index}"
            raise ConfigError(
                f"Invalid company '{label}' in '{path}':\n{exc}"
            ) from exc
        if company.id in seen_ids:
            raise ConfigError(
                f"Duplicate company id '{company.id}' in '{path}'; ids must be unique."
            )
        seen_ids.add(company.id)
        companies.append(company)

    return companies
