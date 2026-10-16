"""Normalization: HTML cleaning, RawJob->CanonicalJob, and content hashing."""

from __future__ import annotations

from jobtracker.normalize.hashing import description_hash
from jobtracker.normalize.html_clean import clean_html
from jobtracker.normalize.normalizer import (
    QuarantineError,
    normalize,
)

__all__ = [
    "QuarantineError",
    "clean_html",
    "description_hash",
    "normalize",
]
