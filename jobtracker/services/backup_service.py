"""Backup / restore for the SQLite database (SPEC Phase 5 operations).

Keeps backup logic out of the CLI so it is unit-testable and reusable. Only
SQLite URLs are supported: the local-first and Fly.io deployments both use a
single SQLite file (on Fly it lives on a mounted volume), so a safe file-level
copy is all that is needed. For any other database engine the caller should use
that engine's native dump tooling, so we fail with a clear message instead of
silently doing the wrong thing.

:func:`backup_database` uses SQLite's online backup API so it is safe to run
while the app/scheduler is live. :func:`restore_database` overwrites the current
database file and is intended to be run with the app stopped.
"""

from __future__ import annotations

import logging
import shutil
import sqlite3
from pathlib import Path

from jobtracker.settings import Settings

logger = logging.getLogger("jobtracker.services.backup")

_SQLITE_PREFIX = "sqlite:///"


class BackupError(RuntimeError):
    """Raised when a backup/restore cannot be performed."""


def sqlite_path(settings: Settings) -> Path:
    """Return the on-disk SQLite file path from ``settings.database_url``.

    Raises :class:`BackupError` for non-SQLite or in-memory URLs.
    """
    url = settings.database_url
    if not url.startswith("sqlite"):
        raise BackupError(
            f"backup/restore only supports SQLite databases, got '{url}'. "
            "For other engines use that engine's native dump/restore tooling."
        )
    if ":memory:" in url or url in ("sqlite://", "sqlite:///:memory:"):
        raise BackupError("Cannot back up an in-memory SQLite database.")
    # sqlite:///relative/path  or  sqlite:////absolute/path
    raw = url[len(_SQLITE_PREFIX):] if url.startswith(_SQLITE_PREFIX) else url.split("///", 1)[-1]
    return Path(raw)


def backup_database(settings: Settings, output: str) -> Path:
    """Back up the SQLite database to ``output`` using the online backup API.

    Returns the destination path. Creates parent directories as needed.
    """
    source = sqlite_path(settings)
    if not source.exists():
        raise BackupError(
            f"Database file '{source}' does not exist yet. Run init-db first."
        )
    dest = Path(output)
    dest.parent.mkdir(parents=True, exist_ok=True)

    src_conn = sqlite3.connect(str(source))
    try:
        dst_conn = sqlite3.connect(str(dest))
        try:
            src_conn.backup(dst_conn)
        finally:
            dst_conn.close()
    finally:
        src_conn.close()
    logger.info("Backed up SQLite database %s -> %s", source, dest)
    return dest


def restore_database(settings: Settings, input_path: str) -> Path:
    """Restore the SQLite database from ``input_path`` (overwrites the target).

    Returns the restored target path. The caller should stop the app/scheduler
    before restoring.
    """
    source = Path(input_path)
    if not source.exists():
        raise BackupError(f"Backup file '{source}' does not exist.")
    target = sqlite_path(settings)
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, target)
    logger.info("Restored SQLite database %s -> %s", source, target)
    return target
