"""AI provider abstraction + Google Gemini implementation (SPEC Phase 3).

The provider layer is intentionally tiny: it turns a prompt string into raw
model text plus token-usage metadata. All prompt building lives in
:mod:`jobtracker.ai.prompts` and all parsing/caching in
:mod:`jobtracker.services.ai_service`, so this module has no knowledge of the
SPEC JSON schema and stays easy to swap for another vendor.

KEY-READY contract:

* :func:`get_provider` returns a :class:`GeminiProvider` whenever
  ``settings.gemini_api_key`` (env ``GEMINI_API_KEY``) is a non-empty string;
  otherwise it returns a :class:`NullProvider`. The key FORMAT is never
  validated: Google AI Studio issues ``AQ.``-prefixed keys.
* Calls go through the official ``google-genai`` SDK to the native Gemini
  endpoint (generativelanguage.googleapis.com), not an OpenAI-compatible one.
* The API key is read exclusively from settings/env. It is NEVER hardcoded or
  logged, and :class:`NullProvider` never performs network I/O.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:  # pragma: no cover - typing only
    from jobtracker.settings import Settings

logger = logging.getLogger("jobtracker.ai.provider")

#: Default Gemini model used when ``settings.gemini_model`` is unset.
DEFAULT_GEMINI_MODEL = "gemini-3.5-flash-lite"


class AIProviderError(RuntimeError):
    """Raised when a configured provider fails to return usable text."""


class AIRateLimitedError(AIProviderError):
    """A short-window (per-minute) rate limit; retrying after a pause works."""

    def __init__(self, message: str, *, retry_after_seconds: float = 30.0) -> None:
        super().__init__(message)
        self.retry_after_seconds = retry_after_seconds


class AIQuotaExhaustedError(AIProviderError):
    """The daily quota is used up; every further call today will fail too."""


@dataclass
class AIResult:
    """Raw provider output: the generated ``text`` plus usage metadata.

    ``available`` is False only for :class:`NullProvider` (no key configured),
    which lets the service layer render a graceful "add a key" message without
    inspecting provider types.
    """

    text: str
    available: bool = True
    provider: str = ""
    model: str = ""
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    total_tokens: int | None = None
    #: Free-form message surfaced to the user (e.g. the no-key guidance).
    message: str | None = None
    raw: dict = field(default_factory=dict)


class AIProvider:
    """Abstract provider turning a prompt into an :class:`AIResult`."""

    name: str = "base"

    @property
    def available(self) -> bool:
        """Whether this provider can actually perform an analysis."""
        return True

    def analyze(self, prompt: str) -> AIResult:  # pragma: no cover - abstract
        raise NotImplementedError


#: Guidance shown wherever AI is unavailable because no key is configured.
NO_KEY_MESSAGE = (
    "AI unavailable - add a Gemini API key. Create a free key at Google AI "
    "Studio (https://aistudio.google.com/apikey) and set GEMINI_API_KEY in "
    "your .env (or as a GitHub/Fly secret), then re-run the analysis."
)

#: Per-request timeout for Gemini calls. Analysis prompts are a few KB and
#: gemini-3.5-flash normally answers in under 10s; this leaves ample headroom
#: while still failing fast enough that one hung call cannot stall a bulk run.
DEFAULT_TIMEOUT_SECONDS = 90.0


class NullProvider(AIProvider):
    """Safe no-op provider used when no API key is configured.

    Never performs network I/O and never raises; returns an :class:`AIResult`
    with ``available=False`` carrying the "add a key" guidance.
    """

    name = "null"

    @property
    def available(self) -> bool:
        return False

    def analyze(self, prompt: str) -> AIResult:  # noqa: ARG002 - prompt unused
        return AIResult(
            text="",
            available=False,
            provider=self.name,
            model="",
            message=NO_KEY_MESSAGE,
        )


class GeminiProvider(AIProvider):
    """Google Gemini provider using the official ``google-genai`` SDK.

    Talks to the native Gemini endpoint (generativelanguage.googleapis.com).
    The key is passed to the SDK client and is never logged. A pre-built
    ``client`` may be injected (tests use this to avoid network I/O).
    """

    name = "gemini"

    def __init__(
        self,
        api_key: str,
        *,
        model: str = DEFAULT_GEMINI_MODEL,
        timeout: float = DEFAULT_TIMEOUT_SECONDS,
        client: Any | None = None,
    ) -> None:
        # Only non-emptiness is checked; the key format is deliberately not
        # validated (AI Studio issues "AQ."-prefixed keys).
        if not api_key or not api_key.strip():
            raise AIProviderError("GeminiProvider requires a non-empty API key.")
        self.model = model or DEFAULT_GEMINI_MODEL
        if client is None:
            from google import genai
            from google.genai import types

            client = genai.Client(
                api_key=api_key.strip(),
                # The SDK expresses the HTTP timeout in milliseconds.
                http_options=types.HttpOptions(timeout=int(timeout * 1000)),
            )
        self._client = client

    @property
    def available(self) -> bool:
        return True

    def analyze(self, prompt: str) -> AIResult:
        """Make one generateContent call and return the response text."""
        from google.genai import errors, types

        config = types.GenerateContentConfig(
            temperature=0.0,
            response_mime_type="application/json",
        )
        try:
            response = self._client.models.generate_content(
                model=self.model, contents=prompt, config=config
            )
        except errors.APIError as exc:
            # The SDK message carries status + reason (e.g. 401
            # ACCESS_TOKEN_TYPE_UNSUPPORTED) but never the key itself.
            message = f"Gemini API returned {exc.code}: {str(exc)[:300]}"
            if exc.code == 429:
                raise _classify_rate_limit(exc, message) from exc
            # 503/500 are transient server-side ("high demand") -- treat like a
            # short rate limit so the bulk pass waits and retries rather than
            # dropping the job.
            if exc.code in (500, 503):
                raise AIRateLimitedError(message, retry_after_seconds=15.0) from exc
            raise AIProviderError(message) from exc
        except Exception as exc:  # noqa: BLE001 - network/timeout failures.
            raise AIProviderError(f"Gemini API request failed: {exc}") from exc

        text = response.text or ""
        if not text.strip():
            raise AIProviderError("Gemini response contained no text.")
        usage = response.usage_metadata
        return AIResult(
            text=text,
            available=True,
            provider=self.name,
            model=self.model,
            prompt_tokens=getattr(usage, "prompt_token_count", None),
            completion_tokens=getattr(usage, "candidates_token_count", None),
            total_tokens=getattr(usage, "total_token_count", None),
        )


def _classify_rate_limit(exc: Any, message: str) -> AIProviderError:
    """Turn a 429 into a daily-quota or a retry-later error.

    Google reports the exhausted quota id (e.g.
    ``GenerateRequestsPerDayPerProjectPerModel-FreeTier``) and a ``retryDelay``
    in the error details. A per-day quota will not recover within the run, so
    the bulk pass must stop; a per-minute one recovers after a short pause.
    """
    details = exc.details if isinstance(getattr(exc, "details", None), dict) else {}
    retry_after = 30.0
    per_day = False
    for item in (details.get("error") or {}).get("details") or []:
        for violation in item.get("violations") or []:
            if "PerDay" in str(violation.get("quotaId", "")):
                per_day = True
        delay = str(item.get("retryDelay") or "")
        if delay.endswith("s"):
            try:
                retry_after = float(delay[:-1])
            except ValueError:
                pass
    if per_day or "PerDay" in str(exc):
        return AIQuotaExhaustedError(message)
    return AIRateLimitedError(message, retry_after_seconds=retry_after)


def get_provider(settings: Settings) -> AIProvider:
    """Return a :class:`GeminiProvider` when a key is set, else NullProvider.

    A real provider is returned whenever ``settings.gemini_api_key`` (env
    ``GEMINI_API_KEY``) is a non-empty string. No prefix/format check is made.
    """
    api_key = (settings.gemini_api_key or "").strip()
    if api_key:
        model = settings.gemini_model or DEFAULT_GEMINI_MODEL
        return GeminiProvider(api_key, model=model)
    return NullProvider()
