"""AI relevance engine (SPEC Phase 3).

Key-ready by design: a real provider runs only when an API key is configured,
otherwise a :class:`~jobtracker.ai.provider.NullProvider` degrades gracefully
with an informative "add a Gemini API key" result instead of crashing. No API
key is ever hardcoded or committed; keys are read only from settings/env.
"""

from __future__ import annotations

from jobtracker.ai.provider import (
    AIProvider,
    AIResult,
    GeminiProvider,
    NullProvider,
    get_provider,
)

__all__ = [
    "AIProvider",
    "AIResult",
    "GeminiProvider",
    "NullProvider",
    "get_provider",
]
