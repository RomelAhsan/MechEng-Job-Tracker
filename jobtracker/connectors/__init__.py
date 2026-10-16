"""ATS connectors: shared HTTP client, base contract, registry, and detection."""

from __future__ import annotations

from jobtracker.connectors.base import Connector, RawJob
from jobtracker.connectors.fallback import REQUIRES_FALLBACK, FallbackConnector
from jobtracker.connectors.http import HttpClient
from jobtracker.connectors.registry import (
    DetectedATS,
    detect_ats,
    get_connector,
    is_supported,
)

__all__ = [
    "REQUIRES_FALLBACK",
    "Connector",
    "DetectedATS",
    "FallbackConnector",
    "HttpClient",
    "RawJob",
    "detect_ats",
    "get_connector",
    "is_supported",
]
