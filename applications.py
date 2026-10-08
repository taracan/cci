"""Job applications: a job seeker applies to a JD, then tracks it.

Multi-tenant Task 4 (tasks 3.2-3.3, requirements R5.1-R5.4, R7.1). This module
holds the business rules only; the HTTP side lives in routes/candidate.py,
which exposes it only when ``config.MULTI_TENANT`` is on.

Where data lives (JSON files only, no database):
  - Applications  -> on the candidate record in candidates.json, newest first:

        "applications": [{"application_id": "app_1a2b3c4d5e6f", "jd_id": "jd_001",
                          "company": "Acme", "status": "applied",
                          "applied_at": "2026-10-06T17:09:50Z"}, ...]

    Records created before this feature have no "applications" key, which is
    treated as an empty list.
  - JD content    -> data/jds/jds.json (unchanged; passed in as a dict).
  - JD's company  -> the JD's ``company`` field, copied onto the application so
    recruiter views can filter by company without re-reading jds.json.
"""
from __future__ import annotations

import uuid
from enum import Enum
from typing import Optional

import auth
from observability.logging_config import get_logger
from timefmt import utc_now_iso

_logger = get_logger("applications")


class ApplyOutcome(Enum):
    """Every possible result of one Apply click.

    Each value is ``(message shown to the seeker, flash CSS category)``, so the
    route can report any outcome without its own if/else chain.
    """

    APPLIED = ("Application sent! You can follow it under My Applications.", "success")  # R5.1
    ALREADY_APPLIED = ("You have already applied to this job.", "info")                # R5.2
    NO_RESUME = ("Please upload your resume before applying.", "warning")              # R5.3
    INACTIVE = ("This job is no longer accepting applications.", "warning")            # R5.4
    NOT_FOUND = ("Job not found.", "danger")
    NO_COMPANY = ("This job is not open for applications yet.", "warning")

    @property
    def message(self) -> str:
        """Human-readable text for the flash banner."""
        return self.value[0]

    @property
    def category(self) -> str:
        """Flash category (success/info/warning/danger), styled in base.html."""
        return self.value[1]


def apply(candidate_id: str, jd: Optional[dict], has_resume: bool) -> ApplyOutcome:
    """Record a job seeker's application to a JD, if every rule allows it.

    Checks run in the order of the design.md section 5 flowchart. The first
    failing check decides the outcome, and nothing is written in that case.

    Args:
        candidate_id: The logged-in seeker's id (e.g. ``cand_1a2b3c4d``).
        jd: The JD dict from jds.json, or None if no JD has that id.
        has_resume: True when the seeker has a parsed resume on file.

    Returns:
        The ApplyOutcome. Only ``APPLIED`` means a new application was saved.
    """
    # Guard checks. Each maps to one requirement and writes nothing.
    if jd is None:
        return ApplyOutcome.NOT_FOUND
    if not has_resume:
        return ApplyOutcome.NO_RESUME
    if not jd.get("is_active", True):
        return ApplyOutcome.INACTIVE

    applied = for_candidate(candidate_id)
    # One application per seeker per JD (R5.2).
    if any(a["jd_id"] == jd["jd_id"] for a in applied):
        return ApplyOutcome.ALREADY_APPLIED

    # Tenant scope (R5.1): the application belongs to the company on the JD.
    # A JD with no company cannot be scoped, so it is refused rather than
    # stored without one.
    company = (jd.get("company") or "").strip()
    if not company:
        return ApplyOutcome.NO_COMPANY

    application = {
        "application_id": f"app_{uuid.uuid4().hex[:12]}",
        "jd_id": jd["jd_id"],
        "company": company,
        "status": "applied",
        "applied_at": utc_now_iso(),
    }
    auth.update_candidate(candidate_id, {"applications": [application, *applied]})

    # IDs only, no names or emails in logs (R9.2).
    _logger.info("application.created", extra={
        "application_id": application["application_id"], "jd_id": application["jd_id"],
    })
    return ApplyOutcome.APPLIED


def for_candidate(candidate_id: str) -> list[dict]:
    """Return one seeker's applications, newest first (R7.1).

    Reads only this seeker's own candidate record, so a seeker can never see
    another person's applications (R7.3).
    """
    cand = auth.get_candidate(candidate_id) or {}
    return list(cand.get("applications", []))
