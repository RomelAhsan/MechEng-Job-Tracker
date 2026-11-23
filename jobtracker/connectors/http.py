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
#: Status codes a bot-protection layer (Cloudflare) uses to refuse a client.
_BLOCK_STATUS = {403, 429, 520, 521, 522, 523, 524}
#: Once this many different hosts under one site (e.g. "myworkdayjobs.com")
#: have refused us in a run, the rest of that site's hosts are skipped for the
#: run instead of each being asked again.
BLOCKED_HOSTS_BEFORE_SKIP = 2


class BlockedError(httpx.HTTPStatusError):
    """The job site refused this client (bot protection / IP throttling).

    Raised without retrying: asking again only adds load and keeps us blocked.
    ``reason`` is a short human-readable explanation for the website.
    """

    def __init__(self, message: str, *, request: httpx.Request, response: httpx.Response,
                 reason: str) -> None:
        super().__init__(message, request=request, response=response)
        self.reason = reason


def site_of(host: str) -> str:
    """The registrable site of ``host`` ("ag.wd3.myworkdayjobs.com" -> "myworkdayjobs.com")."""
    parts = host.lower().split(":", 1)[0].split(".")
    return ".".join(parts[-2:]) if len(parts) >= 2 else host.lower()


def block_reason(response: httpx.Response) -> str | None:
    """Explain a bot-protection refusal, or ``None`` for an ordinary response.

    Only HTML error pages count: Workday answers a closed job with a JSON 403
    ("permission denied"), which is a real answer, not a block.
    """
    if response.status_code not in _BLOCK_STATUS:
        return None
    content_type = response.headers.get("content-type", "").lower()
    if "json" in content_type:
        return None
    server = response.headers.get("server", "").lower()
    if response.status_code == 429:
        return "the job site is refusing our checker (too many requests from GitHub's servers)"
    if response.status_code == 403 and ("cloudflare" in server or "html" in content_type):
        return "the job site's bot protection is blocking our checker"
    if response.status_code >= 520:
        return "the job site's network is refusing connections from GitHub's servers"
    return None


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
        #: Hosts that refused us this run, grouped by site.
        self._blocked_hosts: dict[str, set[str]] = {}
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

    def get_text(self, url: str, *, params: dict | None = None) -> str:
        """GET ``url`` with retry/backoff and return the response body text."""
        response = self.request("GET", url, params=params)
        return response.text

    def post_json(
        self, url: str, json_body: dict, *, params: dict | None = None
    ) -> object:
        """POST a JSON body to ``url`` with retry/backoff; return parsed JSON.

        Shares the same per-host rate limiting + retry/backoff + no-redirect
        policy as :meth:`get_json`. Workday's CXS endpoint requires POST.
        """
        response = self.request("POST", url, params=params, json_body=json_body)
        return response.json()

    def request(
        self,
        method: str,
        url: str,
        *,
        params: dict | None = None,
        json_body: dict | None = None,
    ) -> httpx.Response:
        """Issue a rate-limited request, retrying transient failures.

        A bot-protection refusal raises :class:`BlockedError` at once (no
        retries), and once :data:`BLOCKED_HOSTS_BEFORE_SKIP` hosts of the same
        site have refused us, further requests to that site fail fast without
        being sent.
        """
        host = urlsplit(url).netloc
        site = site_of(host)
        blocked = self._blocked_hosts.get(site, set())
        if len(blocked) >= BLOCKED_HOSTS_BEFORE_SKIP and host not in blocked:
            request = httpx.Request(method, url)
            reason = (
                "the job site is refusing our checker (other boards on this "
                "site already refused it this run)"
            )
            raise BlockedError(
                f"Skipped {host}: {site} is refusing this runner",
                request=request,
                response=httpx.Response(429, request=request),
                reason=reason,
            )

        def _do() -> httpx.Response:
            self._rate_limiter.acquire(host)
            response = self._client.request(
                method, url, params=params, json=json_body
            )
            reason = block_reason(response)
            if reason is not None:
                self._blocked_hosts.setdefault(site, set()).add(host)
                raise BlockedError(
                    f"{response.status_code} from {host}: {reason}",
                    request=response.request,
                    response=response,
                    reason=reason,
                )
            if response.status_code in _RETRYABLE_STATUS:
                response.raise_for_status()
            return response

        return self.retry(_do)

    def blocked_sites(self) -> dict[str, set[str]]:
        """Sites (and their hosts) that refused this client during the run."""
        return {site: set(hosts) for site, hosts in self._blocked_hosts.items()}

    def retry(self, fn: Callable[[], T]) -> T:
        """Run ``fn`` with exponential backoff on 429/5xx/transport errors."""
        attempt = 0
        while True:
            try:
                return fn()
            except BlockedError:
                raise
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
