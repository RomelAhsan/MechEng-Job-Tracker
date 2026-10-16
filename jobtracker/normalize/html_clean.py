"""HTML cleaning helpers (BeautifulSoup + lxml).

Strips scripts/styles, converts to readable plain text, and collapses
whitespace so downstream hashing and heuristics are stable.
"""

from __future__ import annotations

import re

from bs4 import BeautifulSoup

_WHITESPACE_RE = re.compile(r"[ \t\f\v]+")
_BLANKLINES_RE = re.compile(r"\n{3,}")


def clean_html(raw_html: str | None) -> str:
    """Return readable plain text extracted from ``raw_html``.

    Removes ``<script>``/``<style>`` content, treats block elements as line
    breaks, and collapses runs of whitespace. Returns ``""`` for falsy input.
    """
    if not raw_html:
        return ""

    soup = BeautifulSoup(raw_html, "lxml")

    for tag in soup(["script", "style", "noscript"]):
        tag.decompose()

    # <br> and block tags become newlines so paragraph structure survives.
    for br in soup.find_all("br"):
        br.replace_with("\n")
    # List items keep a bullet marker so downstream section parsing can tell
    # requirement/qualification bullets apart from prose paragraphs.
    for li in soup.find_all("li"):
        li.insert_before("\n- ")
        li.append("\n")
    block_tags = ("p", "div", "tr", "h1", "h2", "h3", "h4", "h5", "h6")
    for tag in soup.find_all(block_tags):
        tag.append("\n")

    text = soup.get_text()
    return collapse_whitespace(text)


def collapse_whitespace(text: str) -> str:
    """Collapse spaces/tabs and trim excess blank lines."""
    if not text:
        return ""
    lines = [_WHITESPACE_RE.sub(" ", line).strip() for line in text.splitlines()]
    joined = "\n".join(lines)
    joined = _BLANKLINES_RE.sub("\n\n", joined)
    return joined.strip()
