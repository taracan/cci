"""Tests for Functional Apply + Application Tracker (multi-tenant Task 4, tasks 3.4).

Covers R5.1 happy path, R5.2 duplicate, R5.3 no resume, R5.4 inactive JD,
R7.1 tracker, R7.3 seekers only see their own applications, plus the flag-off
"no regression" contract.

Applications live on the candidate record in candidates.json (no database).
Fixture data (from test_saved_jobs.JDS): jd_a is active (Acme), jd_b is inactive.
"""
import json

import pytest

import auth
import config
# Reuse the logged-in candidate fixture (tmp data dir, parsed resume, no ML).
from tests.test_saved_jobs import cand_client  # noqa: F401  (pytest fixture)


@pytest.fixture()
def seeker(cand_client, monkeypatch):  # noqa: F811
    """Flag ON."""
    monkeypatch.setattr(config, "MULTI_TENANT", True)
    return cand_client


def _apps(client):
    """The logged-in seeker's applications as stored in candidates.json."""
    return auth.get_candidate(client.cid).get("applications", [])


class TestFlagOff:
    """MULTI_TENANT off: candidate pages identical to today; endpoints absent."""

    def test_apply_and_tracker_are_404(self, cand_client):  # noqa: F811
        assert cand_client.post("/candidate/jobs/jd_a/apply").status_code == 404
        assert cand_client.get("/candidate/applications").status_code == 404
        assert _apps(cand_client) == []

    def test_placeholder_button_unchanged(self, cand_client):  # noqa: F811
        html = cand_client.get("/candidate/match-jobs").get_data(as_text=True)
        assert 'class="btn btn-primary btn-apply"' in html
        assert "/apply" not in html and "My Applications" not in html


class TestApply:
    def test_happy_path_scoped_to_jd_company(self, seeker):  # R5.1
        r = seeker.post("/candidate/jobs/jd_a/apply")
        assert r.headers["Location"].endswith("/candidate/applications")
        (app,) = _apps(seeker)
        assert (app["jd_id"], app["company"], app["status"]) == ("jd_a", "Acme", "applied")
        assert app["application_id"].startswith("app_") and app["applied_at"]

    def test_duplicate_is_noop(self, seeker):  # R5.2
        seeker.post("/candidate/jobs/jd_a/apply")
        r = seeker.post("/candidate/jobs/jd_a/apply", follow_redirects=True)
        assert "already applied" in r.get_data(as_text=True)
        assert len(_apps(seeker)) == 1

    def test_no_resume_blocked(self, seeker):  # R5.3
        import routes.candidate as cand_routes
        (cand_routes.PARSED_RESUMES_DIR / f"{seeker.cid}.json").unlink()
        r = seeker.post("/candidate/jobs/jd_a/apply")
        assert r.headers["Location"].endswith("/candidate/upload-resume")
        assert _apps(seeker) == []

    def test_inactive_jd_rejected(self, seeker):  # R5.4
        r = seeker.post("/candidate/jobs/jd_b/apply", follow_redirects=True)
        assert "no longer accepting applications" in r.get_data(as_text=True)
        assert _apps(seeker) == []

    def test_unknown_jd(self, seeker):
        r = seeker.post("/candidate/jobs/nope/apply", follow_redirects=True)
        assert "Job not found" in r.get_data(as_text=True)
        assert _apps(seeker) == []

    def test_jd_without_company_refused(self, seeker):
        import routes.candidate as cand_routes
        jds_file = cand_routes.JDS_DIR / "jds.json"
        jds = json.loads(jds_file.read_text())
        jds[0]["company"] = ""
        jds_file.write_text(json.dumps(jds))
        r = seeker.post("/candidate/jobs/jd_a/apply", follow_redirects=True)
        assert "not open for applications yet" in r.get_data(as_text=True)
        assert _apps(seeker) == []


class TestTracker:
    def test_lists_application_with_status(self, seeker):  # R7.1
        seeker.post("/candidate/jobs/jd_a/apply")
        html = seeker.get("/candidate/applications").get_data(as_text=True)
        assert "Backend Dev" in html and "Acme" in html and "Applied" in html

    def test_job_card_shows_applied_and_nav_link(self, seeker):
        seeker.post("/candidate/jobs/jd_a/apply")
        html = seeker.get("/candidate/match-jobs").get_data(as_text=True)
        assert ">Applied</button>" in html and "My Applications" in html

    def test_other_seekers_rows_hidden(self, seeker):  # R7.3
        candidates = auth.load_json(auth.CANDIDATES_FILE)
        candidates.append({"candidate_id": "cand_other", "applications": [
            {"application_id": "app_x", "jd_id": "jd_a", "company": "Acme",
             "status": "applied", "applied_at": "2026-10-01T00:00:00Z"}]})
        auth.save_json(auth.CANDIDATES_FILE, candidates)
        html = seeker.get("/candidate/applications").get_data(as_text=True)
        assert "haven't applied to any jobs yet" in html
