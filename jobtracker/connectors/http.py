"""Shared HTTP client with per-host rate limiting and exponential backoff.

Connectors share a single :class:`HttpClient` so rate limiting is enforced
across the whole run. ``retry`` reissues requests on 429 / 5xx / transport
errors with exponential backoff. Auth walls are never followed (redirects are
disabled and 401/403 responses are returned, not retried).
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from typing import TypeVar
from urllib.parse import urlsplit

import httpx

from jobtracker.settings import Settings, get_settings

logger = logging.getLogger("jobtracker.http")

T = TypeVar("T")

_RETRYABLE_STATUS = {429, 500, 502, 503, 504}


class PerHostRateLimiter:
    """Minimal per-host throttle enforcing a minimum interval between calls."""

    def __init__(self, rate_per_host: float, *, sleep: Callable[[float], None] = time.sleep):
        # rate_per_host is requests/second; <= 0 disables throttling.
        self._min_interval = 1.0 / rate_per_host if rate_per_host > 0 else 0.0
        self._last_call: dict[str, float] = {}
        self._sleep = sleep
        self._monotonic = time.monotonic

    def acquire(self, host: str) -> None:
        if self._min_interval <= 0:
            return
        now = self._monotonic()
        last = self._last_call.get(host)
        if last is not None:
            wait = self._min_interval - (now - last)
            if wait > 0:
                self._sleep(wait)
        self._last_call[host] = self._monotonic()


class HttpClient:
    """Thin wrapper around :class:`httpx.Client` adding retry + rate limiting."""

    def __init__(
        self,
        settings: Settings | None = None,
        *,
        client: httpx.Client | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.settings = settings or get_settings()
        self.max_retries = self.settings.http_max_retries
        self._sleep = sleep
        self._rate_limiter = PerHostRateLimiter(
            self.settings.rate_limit_per_host, sleep=sleep
        )
        # Never follow redirects: avoids wandering into auth walls.
        self._client = client or httpx.Client(
            timeout=self.settings.http_timeout,
            follow_redirects=False,
            headers={"User-Agent": "MechEng-Job-Tracker/0.1 (+jobtracker)"},
        )

    def __enter__(self) -> HttpClient:
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    def close(self) -> None:
        self._client.close()

    def get_json(self, url: str, *, params: dict | None = None) -> object:
        """GET ``url`` with retry/backoff and return parsed JSON."""
        response = self.request("GET", url, params=params)
        return response.json()

    def request(
        self, method: str, url: str, *, params: dict | None = None
    ) -> httpx.Response:
        """Issue a rate-limited request, retrying transient failures."""
        host = urlsplit(url).netloc

        def _do() -> httpx.Response:
            self._rate_limiter.acquire(host)
            response = self._client.request(method, url, params=params)
            if response.status_code in _RETRYABLE_STATUS:
                response.raise_for_status()
            return response

        return self.retry(_do)

    def retry(self, fn: Callable[[], T]) -> T:
        """Run ``fn`` with exponential backoff on 429/5xx/transport errors."""
        attempt = 0
        while True:
            try:
                return fn()
            except (httpx.TransportError, httpx.HTTPStatusError) as exc:
                if isinstance(exc, httpx.HTTPStatusError):
                    status = exc.response.status_code
                    if status not in _RETRYABLE_STATUS:
                        raise
                if attempt >= self.max_retries:
                    logger.warning("Giving up after %d retries: %s", attempt, exc)
                    raise
                backoff = 2.0**attempt
                logger.info(
                    "Transient HTTP error (%s); retry %d/%d in %.1fs",
                    exc,
                    attempt + 1,
                    self.max_retries,
                    backoff,
                )
                self._sleep(backoff)
                attempt += 1
