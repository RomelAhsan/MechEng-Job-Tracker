"""Deterministic SHA-256 content hashing for change detection."""

from __future__ import annotations

import hashlib
from collections.abc import Iterable


def description_hash(
    description: str,
    title: str,
    location: str,
    requirements: Iterable[str],
) -> str:
    """Return a stable SHA-256 hex digest over the normalized content.

    Hash inputs are the normalized ``description``, ``title``, ``location`` and
    ``requirements`` list (in order). Used for change detection and the future
    AI cache key, so the output must be deterministic for identical input.
    """
    parts = [
        description or "",
        title or "",
        location or "",
        "\n".join(requirements or []),
    ]
    payload = "\x1f".join(parts)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()
