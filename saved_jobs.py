"""Saved jobs — candidates bookmark JDs to revisit from their profile.

Stored on the candidate record in candidates.json as:

    "saved_jobs": [{"jd_id": "jd_001", "saved_at": "2026-09-24T19:05:11"}, ...]

Newest saves are kept first. Records created before this feature simply have
no "saved_jobs" key, which is treated as an empty list.
"""
from __future__ import annotations

import auth
from timefmt import utc_now_iso


def get_saved(candidate_id: str) -> list[dict]:
    """Return the candidate's saved-job entries (newest first)."""
    cand = auth.get_candidate(candidate_id) or {}
    return list(cand.get("saved_jobs", []))


def saved_ids(candidate_id: str) -> set[str]:
    """Return just the saved JD ids, for quick "is this saved?" checks."""
    return {s["jd_id"] for s in get_saved(candidate_id)}


def toggle_saved(candidate_id: str, jd_id: str) -> bool:
    """Save the job if it isn't saved yet, otherwise unsave it.

    Args:
        candidate_id: The logged-in candidate.
        jd_id: The job description to (un)save.

    Returns:
        True if the job is saved after the call, False if it was removed.
    """
    saved = get_saved(candidate_id)
    if any(s["jd_id"] == jd_id for s in saved):
        saved = [s for s in saved if s["jd_id"] != jd_id]
        now_saved = False
    else:
        saved.insert(0, {"jd_id": jd_id, "saved_at": utc_now_iso()})
        now_saved = True
    auth.update_candidate(candidate_id, {"saved_jobs": saved})
    return now_saved
