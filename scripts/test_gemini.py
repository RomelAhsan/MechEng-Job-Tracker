"""One-shot Gemini connectivity check.

Reads the key from GEMINI_API_KEY (exported in the shell, or saved in the
gitignored .env file), calls the native Gemini endpoint through the official
google-genai SDK, and prints the reply. The model comes from GEMINI_MODEL and
defaults to gemini-3.5-flash-lite. The key format is NOT validated beyond being
non-empty: AI Studio issues "AQ."-prefixed keys and that is expected.

Run from the repository root:
    python scripts/test_gemini.py
"""

from __future__ import annotations

import sys


def main() -> int:
    # Settings read GEMINI_API_KEY / GEMINI_MODEL from the environment first,
    # then from .env. Nothing is hardcoded.
    from jobtracker.settings import get_settings

    settings = get_settings()
    api_key = (settings.gemini_api_key or "").strip()
    model = settings.gemini_model

    # Only check that the key is a non-empty string; no prefix check.
    if not api_key:
        print("GEMINI_API_KEY is not set (or is empty). Add it to .env and re-run.")
        return 2

    from google import genai

    client = genai.Client(api_key=api_key)
    try:
        # Exactly one generateContent call.
        response = client.models.generate_content(
            model=model,
            contents="Reply with exactly: Gemini connection OK.",
        )
    except Exception as exc:  # noqa: BLE001 - surface the exact error verbatim.
        print("Gemini call FAILED. Exact error below:")
        print(f"{type(exc).__name__}: {exc}")
        return 1

    print(f"Model: {model}")
    print("Response text:")
    print(response.text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
