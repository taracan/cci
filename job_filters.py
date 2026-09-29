"""Filters and sorting for the Find Jobs page.

Filter options are built from the jobs being shown, every time the page loads,
so there is no separate list to maintain:

  * Location — one option per distinct place. "Hyderabad, Remote" counts as two
    places. Case/spacing differences collapse into one option, and remote-style
    values ("remote", "WFH", "Work from home") all become a single "Remote".
    A new location in a future JD appears automatically; an existing one is
    never duplicated.
  * Experience — fixed buckets (0–3, 4–6, 7–9, 10+ years) on the JD's
    required years. Only buckets that contain jobs are shown.
  * Salary — fixed buckets per currency on the yearly amount (monthly salaries
    are multiplied by 12). A job whose range spans two buckets appears in both.
    Jobs without a salary go in "Not disclosed".

Within one filter, choices are OR-ed (Pune OR Remote); across filters they are
AND-ed (Pune/Remote AND 4–6 years).
"""
from __future__ import annotations

import re
from typing import Iterable, Mapping, Optional

from salary import CURRENCIES
from timefmt import parse_utc

# ---------------------------------------------------------------------------
# Location
# ---------------------------------------------------------------------------

REMOTE_KEY = "remote"
_REMOTE_ALIASES = {"remote", "wfh", "work from home", "anywhere", "fully remote", "remote only"}
_LOCATION_SPLIT = re.compile(r"\s*(?:,|/|\||;|\bor\b|&)\s*", re.IGNORECASE)


def _loc_key(part: str) -> str:
    """Normalised key for one place: lowercase, single spaces, remote aliases → 'remote'."""
    key = " ".join(part.lower().split())
    return REMOTE_KEY if key in _REMOTE_ALIASES else key


def _loc_label(part: str) -> str:
    """Display label: 'Remote' for remote, else the text (title-cased if all lowercase)."""
    if _loc_key(part) == REMOTE_KEY:
        return "Remote"
    text = " ".join(part.split())
    return text.title() if text.islower() else text


def location_parts(location: Optional[str]) -> list[tuple[str, str]]:
    """Split a JD location into [(key, label), ...], e.g. 'Hyderabad, Remote'."""
    parts = [p for p in _LOCATION_SPLIT.split(location or "") if p.strip()]
    seen, out = set(), []
    for p in parts:
        k = _loc_key(p)
        if k not in seen:
            seen.add(k)
            out.append((k, _loc_label(p)))
    return out


# ---------------------------------------------------------------------------
# Experience buckets  (key, label, min_years, max_years or None)
# ---------------------------------------------------------------------------

EXPERIENCE_BUCKETS = [
    ("0-3", "0–3 years", 0, 3),
    ("4-6", "4–6 years", 4, 6),
    ("7-9", "7–9 years", 7, 9),
    ("10+", "10+ years", 10, None),
]


def experience_bucket(years) -> Optional[str]:
    """Bucket key for a JD's required years (0 falls in '0-3')."""
    try:
        y = int(float(years or 0))
    except (TypeError, ValueError):
        return None
    for key, _label, lo, hi in EXPERIENCE_BUCKETS:
        if y >= lo and (hi is None or y <= hi):
            return key
    return None


# ---------------------------------------------------------------------------
# Salary buckets  — yearly amounts, per currency: (key, label, min, max or None)
# ---------------------------------------------------------------------------

NOT_DISCLOSED = "none"

_INR_BUCKETS = [
    ("inr-0-5", "Under ₹5L", 0, 500_000),
    ("inr-5-10", "₹5L – ₹10L", 500_000, 1_000_000),
    ("inr-10-20", "₹10L – ₹20L", 1_000_000, 2_000_000),
    ("inr-20-35", "₹20L – ₹35L", 2_000_000, 3_500_000),
    ("inr-35+", "₹35L+", 3_500_000, None),
]


def _k_buckets(code: str) -> list[tuple]:
    s = CURRENCIES[code]
    c = code.lower()
    return [
        (f"{c}-0-50", f"Under {s}50K", 0, 50_000),
        (f"{c}-50-100", f"{s}50K – {s}100K", 50_000, 100_000),
        (f"{c}-100-150", f"{s}100K – {s}150K", 100_000, 150_000),
        (f"{c}-150+", f"{s}150K+", 150_000, None),
    ]


SALARY_BUCKETS = {"INR": _INR_BUCKETS, "USD": _k_buckets("USD"),
                  "EUR": _k_buckets("EUR"), "GBP": _k_buckets("GBP")}


def yearly_salary_range(jd: Mapping) -> Optional[tuple[str, int, int]]:
    """(currency, yearly_low, yearly_high) or None when salary isn't disclosed."""
    lo, hi = jd.get("salary_min"), jd.get("salary_max")
    if lo is None and hi is None:
        return None
    lo = hi if lo is None else lo
    hi = lo if hi is None else hi
    factor = 12 if jd.get("salary_period") == "month" else 1
    return (jd.get("salary_currency") or "INR", int(lo) * factor, int(hi) * factor)


def salary_buckets(jd: Mapping) -> list[str]:
    """All salary bucket keys a JD falls into (its range may span several)."""
    rng = yearly_salary_range(jd)
    if rng is None:
        return [NOT_DISCLOSED]
    currency, lo, hi = rng
    keys = [k for k, _l, b_lo, b_hi in SALARY_BUCKETS.get(currency, [])
            if hi >= b_lo and (b_hi is None or lo < b_hi)]
    return keys or [NOT_DISCLOSED]


# ---------------------------------------------------------------------------
# Facets (the options + counts shown in the filter bar)
# ---------------------------------------------------------------------------

def build_facets(jds: Iterable[Mapping]) -> dict:
    """Build filter options with job counts from the given JDs.

    Returns:
        {"location": [...], "experience": [...], "salary": [...]} where each
        option is {"key", "label", "count"}. Only options with jobs are included.
    """
    jds = list(jds)

    loc_counts: dict[str, int] = {}
    loc_labels: dict[str, str] = {}
    for jd in jds:
        for key, label in location_parts(jd.get("location")):
            loc_counts[key] = loc_counts.get(key, 0) + 1
            loc_labels.setdefault(key, label)
    # Remote first, then A–Z.
    loc_keys = sorted(loc_counts, key=lambda k: (k != REMOTE_KEY, loc_labels[k].lower()))
    locations = [{"key": k, "label": loc_labels[k], "count": loc_counts[k]} for k in loc_keys]

    exp_counts: dict[str, int] = {}
    for jd in jds:
        b = experience_bucket(jd.get("experience_years"))
        if b:
            exp_counts[b] = exp_counts.get(b, 0) + 1
    experience = [{"key": k, "label": label, "count": exp_counts[k]}
                  for k, label, _lo, _hi in EXPERIENCE_BUCKETS if k in exp_counts]

    sal_counts: dict[str, int] = {}
    for jd in jds:
        for b in salary_buckets(jd):
            sal_counts[b] = sal_counts.get(b, 0) + 1
    salary = [{"key": k, "label": label, "count": sal_counts[k]}
              for code in SALARY_BUCKETS for k, label, _lo, _hi in SALARY_BUCKETS[code]
              if k in sal_counts]
    if NOT_DISCLOSED in sal_counts:
        salary.append({"key": NOT_DISCLOSED, "label": "Not disclosed", "count": sal_counts[NOT_DISCLOSED]})

    return {"location": locations, "experience": experience, "salary": salary}


# ---------------------------------------------------------------------------
# Applying filters + sorting
# ---------------------------------------------------------------------------

SORT_OPTIONS = [
    ("match", "Best match"),
    ("salary_high", "Salary: high to low"),
    ("salary_low", "Salary: low to high"),
    ("exp_low", "Experience: low to high"),
    ("exp_high", "Experience: high to low"),
    ("newest", "Newest first"),          # by last activity: updated, else posted
]
_SORT_KEYS = {k for k, _ in SORT_OPTIONS}


def read_selection(args) -> dict:
    """Read ?loc=..&exp=..&sal=..&sort=.. from request.args (a MultiDict)."""
    sort = args.get("sort", "match")
    return {
        "loc": [v for v in args.getlist("loc") if v],
        "exp": [v for v in args.getlist("exp") if v],
        "sal": [v for v in args.getlist("sal") if v],
        "sort": sort if sort in _SORT_KEYS else "match",
    }


def matches_selection(jd: Mapping, sel: dict) -> bool:
    """True if a JD passes every active filter."""
    if sel["loc"] and not {k for k, _ in location_parts(jd.get("location"))} & set(sel["loc"]):
        return False
    if sel["exp"] and experience_bucket(jd.get("experience_years")) not in sel["exp"]:
        return False
    if sel["sal"] and not set(salary_buckets(jd)) & set(sel["sal"]):
        return False
    return True


def _salary_sort_value(jd: Mapping, high: bool) -> tuple:
    """Disclosed salaries first (INR group, then others), undisclosed last."""
    rng = yearly_salary_range(jd)
    if rng is None:
        return (1, 0, 0)
    currency, lo, hi = rng
    amount = hi if high else lo
    return (0, currency != "INR", -amount if high else amount)


_EPOCH = parse_utc("1970-01-01T00:00:00")


def _last_activity(jd: Mapping):
    """When a JD last changed: updated_at if set, otherwise created_at (posted)."""
    return parse_utc(jd.get("updated_at")) or parse_utc(jd.get("created_at")) or _EPOCH


def filter_and_sort(matches: list[dict], sel: dict) -> list[dict]:
    """Apply the selection to match items ({"jd", "result", ...}) and sort them.

    Ties always fall back to the overall match score (best first).
    """
    items = [m for m in matches if matches_selection(m["jd"], sel)]
    score = lambda m: -m["result"]["overall_score"]  # noqa: E731
    exp = lambda m: int(m["jd"].get("experience_years") or 0)  # noqa: E731

    sort = sel["sort"]
    if sort == "salary_high":
        items.sort(key=lambda m: (_salary_sort_value(m["jd"], True), score(m)))
    elif sort == "salary_low":
        items.sort(key=lambda m: (_salary_sort_value(m["jd"], False), score(m)))
    elif sort == "exp_low":
        items.sort(key=lambda m: (exp(m), score(m)))
    elif sort == "exp_high":
        items.sort(key=lambda m: (-exp(m), score(m)))
    elif sort == "newest":
        items.sort(key=lambda m: (_last_activity(m["jd"]), -score(m)), reverse=True)
    else:
        items.sort(key=score)
    return items


def active_chips(sel: dict, facets: dict) -> list[dict]:
    """Chips for the active filters: [{"param", "value", "label"}]."""
    # Fixed bucket labels first, so a chip reads "7–9 years" even when that
    # bucket currently has no jobs (e.g. an old bookmarked URL).
    labels = {("exp", k): label for k, label, _lo, _hi in EXPERIENCE_BUCKETS}
    labels.update({("sal", k): label for bs in SALARY_BUCKETS.values() for k, label, _lo, _hi in bs})
    labels[("sal", NOT_DISCLOSED)] = "Not disclosed"
    labels.update({("loc", o["key"]): o["label"] for o in facets["location"]})
    labels.update({("exp", o["key"]): o["label"] for o in facets["experience"]})
    labels.update({("sal", o["key"]): o["label"] for o in facets["salary"]})
    chips = []
    for param in ("loc", "exp", "sal"):
        for v in sel[param]:
            fallback = _loc_label(v) if param == "loc" else v
            chips.append({"param": param, "value": v, "label": labels.get((param, v), fallback)})
    return chips
