"""AI provider abstraction + Google Gemini implementation (SPEC Phase 3).

The provider layer is intentionally tiny: it turns a prompt string into raw
model text plus token-usage metadata. All prompt building lives in
:mod:`jobtracker.ai.prompts` and all parsing/caching in
:mod:`jobtracker.services.ai_service`, so this module has no knowledge of the
SPEC JSON schema and stays easy to swap for another vendor.

KEY-READY contract:

* :func:`get_provider` returns a :class:`GeminiProvider` ONLY when
  ``settings.ai_provider == "gemini"`` AND ``settings.ai_api_key`` is set;
  otherwise it returns a :class:`NullProvider`.
* The API key is read exclusively from settings/env. It is NEVER hardcoded or
  logged, and :class:`NullProvider` never performs network I/O.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover - typing only
    from jobtracker.settings import Settings

logger = logging.getLogger("jobtracker.ai.provider")

#: Default Gemini model used when ``settings.ai_model`` is unset.
DEFAULT_GEMINI_MODEL = "gemini-1.5-flash"

#: REST base for the Google Generative Language API (v1beta generateContent).
_GEMINI_BASE = "https://generativelanguage.googleapis.com/v1beta"


class AIProviderError(RuntimeError):
    """Raised when a configured provider fails to return usable text."""


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
    "Studio (https://aistudio.google.com/app/apikey), set AI_PROVIDER=gemini "
    "and AI_API_KEY=AIza... in your .env, then re-run the analysis."
)


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
    """Google Gemini provider using httpx against the REST generateContent API.

    The API key is supplied at construction from settings and sent only as the
    ``x-goog-api-key`` request header. It is never logged.
    """

    name = "gemini"

    def __init__(
        self,
        api_key: str,
        *,
        model: str = DEFAULT_GEMINI_MODEL,
        timeout: float = 30.0,
    ) -> None:
        if not api_key:
            raise AIProviderError("GeminiProvider requires a non-empty API key.")
        self._api_key = api_key
        self.model = model or DEFAULT_GEMINI_MODEL
        self._timeout = timeout

    @property
    def available(self) -> bool:
        return True

    def analyze(self, prompt: str) -> AIResult:
        """Call generateContent and return the first candidate's text."""
        import httpx

        url = f"{_GEMINI_BASE}/models/{self.model}:generateContent"
        payload = {
            "contents": [{"role": "user", "parts": [{"text": prompt}]}],
            "generationConfig": {
                "temperature": 0.2,
                "responseMimeType": "application/json",
            },
        }
        headers = {
            "x-goog-api-key": self._api_key,
            "Content-Type": "application/json",
        }
        try:
            response = httpx.post(
                url, json=payload, headers=headers, timeout=self._timeout
            )
            response.raise_for_status()
            data = response.json()
        except httpx.HTTPStatusError as exc:
            # Do not leak the key; surface a trimmed body for debugging.
            body = exc.response.text[:300] if exc.response is not None else ""
            raise AIProviderError(
                f"Gemini API returned {exc.response.status_code}: {body}"
            ) from exc
        except httpx.HTTPError as exc:
            raise AIProviderError(f"Gemini API request failed: {exc}") from exc

        text = _extract_text(data)
        usage = data.get("usageMetadata") or {}
        return AIResult(
            text=text,
            available=True,
            provider=self.name,
            model=self.model,
            prompt_tokens=usage.get("promptTokenCount"),
            completion_tokens=usage.get("candidatesTokenCount"),
            total_tokens=usage.get("totalTokenCount"),
            raw=data,
        )


def _extract_text(data: dict) -> str:
    """Pull the concatenated text parts from a generateContent response."""
    candidates = data.get("candidates") or []
    if not candidates:
        raise AIProviderError("Gemini response contained no candidates.")
    parts = (candidates[0].get("content") or {}).get("parts") or []
    text = "".join(part.get("text", "") for part in parts)
    if not text.strip():
        raise AIProviderError("Gemini response contained no text.")
    return text


def get_provider(settings: Settings) -> AIProvider:
    """Return a :class:`GeminiProvider` when fully configured, else NullProvider.

    A real provider is returned ONLY when ``ai_provider == "gemini"`` AND an
    ``ai_api_key`` is present. Any other configuration (no key, a different
    provider name) yields the graceful :class:`NullProvider`.
    """
    provider = (settings.ai_provider or "").strip().lower()
    api_key = (settings.ai_api_key or "").strip()
    if provider == "gemini" and api_key:
        model = getattr(settings, "ai_model", None) or DEFAULT_GEMINI_MODEL
        return GeminiProvider(api_key, model=model, timeout=settings.http_timeout)
    return NullProvider()
