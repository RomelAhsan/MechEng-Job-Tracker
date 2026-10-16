"""Fail-soft fallback for unsupported ATS types.

Handles ``workable``/``rss``/``sitemap``/``json_ld``/``html``: returns no jobs,
flags the source as ``requires_fallback`` so Source Health surfaces it, logs a
clear message, and never raises. Keeps the pipeline alive until a real
connector exists.
"""

from __future__ import annotations

import logging

from jobtracker.connectors.base import Connector, RawJob
from jobtracker.schemas.company import CompanyConfig

logger = logging.getLogger("jobtracker.connectors.fallback")

#: Sentinel stored on source_health.last_status when a source needs a connector.
REQUIRES_FALLBACK = "requires_fallback"


class FallbackConnector(Connector):
    """Stub connector for ATS types without a real implementation yet."""

    ats = "fallback"

    def __init__(self) -> None:
        #: Set after :meth:`fetch` so the caller can update source health.
        self.last_status = REQUIRES_FALLBACK

    def fetch(self, company: CompanyConfig) -> list[RawJob]:
        """Return no jobs and mark the source as requiring a fallback."""
        self.last_status = REQUIRES_FALLBACK
        logger.info(
            "No connector for ats=%r (company=%s); marking %s.",
            company.ats,
            company.id,
            REQUIRES_FALLBACK,
        )
        return []
