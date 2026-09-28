"""Turn free-form job description text into short display points.

JDs arrive either as bullet lists (pasted or extracted from a PDF/DOCX) or as
plain paragraphs. The job card shows the first few points and hides the rest
behind a "show more" toggle, so we need a list either way:

  - If the text has several bullet/numbered lines, each line is a point.
  - Otherwise the text is split into sentences, one point per sentence.
"""
from __future__ import annotations

import re

# A line that starts with a bullet or a number, e.g. "- ", "• ", "* ", "1. ", "2) "
_BULLET_RE = re.compile(r"^\s*(?:[-•*·▪●◦–]|\d{1,2}[.)])\s+")
# Sentence boundary: . ! or ? followed by whitespace and a capital letter / digit.
_SENTENCE_RE = re.compile(r"(?<=[.!?])\s+(?=[A-Z0-9\"'(])")


def _clean(point: str) -> str:
    """Strip bullet markers and surrounding whitespace from one point."""
    return _BULLET_RE.sub("", point).strip()


def description_points(text: str | None) -> list[str]:
    """Split JD text into display points (bullets if present, else sentences).

    Args:
        text: The raw job description text (may be empty or None).

    Returns:
        A list of non-empty point strings, in their original order.
    """
    if not text or not text.strip():
        return []

    lines = [ln for ln in text.splitlines() if ln.strip()]
    bullet_lines = [ln for ln in lines if _BULLET_RE.match(ln)]

    # Treat as a bullet list when at least two lines are bullets.
    if len(bullet_lines) >= 2:
        points = [_clean(ln) for ln in lines]
    else:
        flat = " ".join(ln.strip() for ln in lines)
        points = [_clean(s) for s in _SENTENCE_RE.split(flat)]

    return [p for p in points if p]
