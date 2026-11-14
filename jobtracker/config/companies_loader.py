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


#: Ordered keys emitted per company entry, matching the existing shape of
#: ``config/companies.yaml``. Optional provider-specific keys
#: (tenant/data_center/site/itar_restricted) are appended only when set.
_BASE_KEYS = (
    "id",
    "name",
    "ats",
    "board_token",
    "careers_url",
    "enabled",
    "priority",
    "check_interval_minutes",
    "locations",
    "notes",
)
_OPTIONAL_KEYS = ("tenant", "data_center", "site", "itar_restricted")


def _entry_from_config(config: CompanyConfig) -> dict[str, object]:
    """Serialize a :class:`CompanyConfig` into the YAML entry shape.

    ``use_enum_values`` means ``ats``/``priority`` are already plain strings.
    Only non-falsy optional keys are written so existing entries are not
    bloated with ``tenant: null`` noise.
    """
    data = config.model_dump(mode="json")
    entry: dict[str, object] = {}
    for key in _BASE_KEYS:
        entry[key] = data.get(key)
    for key in _OPTIONAL_KEYS:
        value = data.get(key)
        if value:
            entry[key] = value
    return entry


def append_or_update_yaml(path: str | Path, config: CompanyConfig) -> Path:
    """Upsert ``config`` into the companies YAML at ``path`` (idempotent).

    Loads the current ``companies:`` list, replaces the entry whose ``id``
    matches (updating in place rather than duplicating) or appends a new one,
    validates every resulting entry through :class:`CompanyConfig`, and writes
    the file back in the existing key shape. Adding the same company twice
    updates it and never creates a duplicate.
    """
    file_path = Path(path)
    if file_path.exists():
        data = _read_yaml(file_path)
        if not isinstance(data, dict) or not isinstance(data.get("companies"), list):
            raise ConfigError(
                f"'{file_path}' must contain a top-level 'companies:' list."
            )
        companies = [e for e in data["companies"] if isinstance(e, dict)]
    else:
        companies = []

    new_entry = _entry_from_config(config)
    replaced = False
    for index, entry in enumerate(companies):
        if entry.get("id") == config.id:
            companies[index] = new_entry
            replaced = True
            break
    if not replaced:
        companies.append(new_entry)

    # Validate every entry so a malformed upsert never corrupts the seed file.
    for entry in companies:
        CompanyConfig(**entry)

    file_path.parent.mkdir(parents=True, exist_ok=True)
    with file_path.open("w", encoding="utf-8") as handle:
        yaml.safe_dump(
            {"companies": companies},
            handle,
            sort_keys=False,
            default_flow_style=False,
            allow_unicode=True,
        )
    return file_path


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
