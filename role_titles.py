"""Normalise job/role titles so the role score compares meaning, not spelling.

Used by ``matching_engine.calculate_role_relevance`` to turn a candidate's
target role and a JD's role title into comparable sets of words.

Three steps, in order:

1. **Punctuation** — tech names that contain symbols are protected first
   (``Node.js`` → ``nodejs``, ``C++`` → ``cpp``, ``C#`` → ``csharp``,
   ``.NET`` → ``dotnet``). Then periods are dropped (``Sr.`` → ``sr``,
   ``A.I.`` → ``ai``) and every other symbol becomes a space
   (``AI/ML`` → ``ai ml``, ``Full-Stack`` → ``full stack``).
2. **Synonyms** — words and phrases are mapped to one canonical form, longest
   phrase first (``machine learning`` → ``ml``, ``full stack`` → ``fullstack``,
   ``sr`` → ``senior``, ``swe`` → ``software engineer``). Multi-word phrases
   collapse to a single token so "ML Engineer" and "Machine Learning
   Engineer" end up with the same number of words and score 100.
3. **Filler words** (``a``, ``the``, ``of`` …) are removed.

To teach the matcher a new synonym, add a line to ``SYNONYMS`` below:
keys are the words as they appear after step 1, values are the canonical
words to use instead.
"""
from __future__ import annotations

import re

# Words ignored when comparing titles.
FILLER_WORDS = frozenset({"a", "an", "the", "of", "in", "for", "and", "or", "at", "to", "with"})

# Tech names whose punctuation carries meaning. Applied before other symbols are stripped.
_PROTECTED = [
    (re.compile(r"\bnode\s*\.?\s*js\b"), " nodejs "),
    (re.compile(r"\b([a-z]+)\.js\b"), r" \1js "),         # vue.js → vuejs, next.js → nextjs
    (re.compile(r"\bc\s*\+\+"), " cpp "),
    (re.compile(r"\bc\s*#"), " csharp "),
    (re.compile(r"\bf\s*#"), " fsharp "),
    (re.compile(r"(?<![a-z0-9])\.net\b"), " dotnet "),
]

# Canonical forms. Keys: space-separated words (after punctuation handling).
# Values: the canonical word(s) that replace them.
SYNONYMS: dict[str, str] = {
    # --- AI / data ---
    "machine learning": "ml",
    "artificial intelligence": "ai",
    "deep learning": "dl",
    "natural language processing": "nlp",
    "computer vision": "cv",
    "large language model": "llm",
    "large language models": "llm",
    "llms": "llm",
    "generative ai": "genai",
    "gen ai": "genai",
    "business intelligence": "bi",
    "ml ops": "mlops",
    "mle": "ml engineer",
    # --- software / web ---
    "full stack": "fullstack",
    "front end": "frontend",
    "back end": "backend",
    "dev ops": "devops",
    "software development engineer": "software engineer",
    "swe": "software engineer",
    "sde": "software engineer",
    "sw": "software",
    "dev": "developer",
    "devs": "developer",
    "js": "javascript",
    "ts": "typescript",
    "golang": "go",
    "e commerce": "ecommerce",
    "user interface": "ui",
    "user experience": "ux",
    # --- ops / QA / infra ---
    "site reliability engineer": "sre",
    "site reliability engineering": "sre",
    "quality assurance": "qa",
    "sysadmin": "system administrator",
    "sys admin": "system administrator",
    "admin": "administrator",
    "sys": "system",
    "ops": "operations",
    "information technology": "it",
    # --- seniority / titles ---
    "sr": "senior",
    "snr": "senior",
    "jr": "junior",
    "assoc": "associate",
    "asst": "assistant",
    "engr": "engineer",
    "mgr": "manager",
    "mgmt": "management",
    "vice president": "vp",
    "chief technology officer": "cto",
    "chief executive officer": "ceo",
    "human resources": "hr",
    "co founder": "cofounder",
}

# Lookup table keyed by word tuples, plus the longest phrase length.
_SYNONYM_TABLE: dict[tuple[str, ...], tuple[str, ...]] = {
    tuple(k.split()): tuple(v.split()) for k, v in SYNONYMS.items()
}
_MAX_PHRASE = max(len(k) for k in _SYNONYM_TABLE)


def tokenize(title: str | None) -> list[str]:
    """Lowercase a title and split it into words, handling punctuation.

    Args:
        title: A role title such as "Sr. Full-Stack Dev (Node.js)".

    Returns:
        The words in order, e.g. ["sr", "full", "stack", "dev", "nodejs"].
    """
    text = (title or "").lower()
    for pattern, replacement in _PROTECTED:
        text = pattern.sub(replacement, text)
    text = text.replace(".", "").replace("'", "").replace("’", "")
    text = re.sub(r"[^a-z0-9]+", " ", text)
    return text.split()


def apply_synonyms(words: list[str]) -> list[str]:
    """Replace known words/phrases with their canonical form (longest match first).

    Each word is rewritten at most once, so canonical forms are never re-mapped.
    """
    out: list[str] = []
    i = 0
    while i < len(words):
        for n in range(min(_MAX_PHRASE, len(words) - i), 0, -1):
            replacement = _SYNONYM_TABLE.get(tuple(words[i:i + n]))
            if replacement is not None:
                out.extend(replacement)
                i += n
                break
        else:
            out.append(words[i])
            i += 1
    return out


# Separators between several target roles typed into one field:
# comma, semicolon, pipe, new line, or the word "or". NOT "/" — "AI/ML Engineer" is one role.
_ROLE_SPLIT = re.compile(r"\s*(?:[,;|\n]|\bor\b)\s*", re.IGNORECASE)


def split_target_roles(text: str | None) -> list[str]:
    """Split a candidate's target-role field into separate roles, in the order typed.

    Examples:
        "Data Analyst, ML Engineer"       → ["Data Analyst", "ML Engineer"]
        "Data Analyst or ML Engineer"     → ["Data Analyst", "ML Engineer"]
        "AI/ML Engineer; Data Scientist"  → ["AI/ML Engineer", "Data Scientist"]
        "" / None                         → []

    Blank parts are dropped and repeats (ignoring case/spacing) are kept once.
    """
    roles: list[str] = []
    seen: set[str] = set()
    for part in _ROLE_SPLIT.split(text or ""):
        role = " ".join(part.split())
        if role and role.lower() not in seen:
            seen.add(role.lower())
            roles.append(role)
    return roles


def normalize_role(title: str | None) -> set[str]:
    """Return the set of canonical, non-filler words in a role title.

    Examples:
        "ML Engineer"                → {"ml", "engineer"}
        "Machine Learning Engineer"  → {"ml", "engineer"}
        "Sr. Full-Stack Dev"         → {"senior", "fullstack", "developer"}
        "AI/LLM Engineer"            → {"ai", "llm", "engineer"}
    """
    return {w for w in apply_synonyms(tokenize(title)) if w not in FILLER_WORDS}
