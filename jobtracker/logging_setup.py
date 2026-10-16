"""Logging configuration: stdout + optional rotating file."""

from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler

from jobtracker.settings import Settings

_LOG_FORMAT = "%(asctime)s %(levelname)s %(name)s %(message)s"


def configure(settings: Settings) -> None:
    """Configure the root logger from settings.

    Logs to stdout and, when ``log_file`` is set, to a rotating file handler.
    Safe to call more than once; existing handlers are cleared first.
    """
    level = getattr(logging, settings.log_level.upper(), logging.INFO)

    root = logging.getLogger()
    root.setLevel(level)
    for handler in list(root.handlers):
        root.removeHandler(handler)

    formatter = logging.Formatter(_LOG_FORMAT)

    stream_handler = logging.StreamHandler()
    stream_handler.setFormatter(formatter)
    root.addHandler(stream_handler)

    if settings.log_file:
        file_handler = RotatingFileHandler(
            settings.log_file, maxBytes=5 * 1024 * 1024, backupCount=3
        )
        file_handler.setFormatter(formatter)
        root.addHandler(file_handler)


def company_logger(company_id: str) -> logging.LoggerAdapter:
    """Return a logger adapter that tags records with a company id.

    Used during ingestion runs for per-company contextual logging.
    """
    logger = logging.getLogger("jobtracker.ingest")
    return logging.LoggerAdapter(logger, {"company_id": company_id})
