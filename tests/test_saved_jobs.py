"""Tests for the job card: description points, Save/unsave, and Saved Jobs on the profile."""
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from jd_text import description_points  # noqa: E402


# ---------------------------------------------------------------------------
# description_points
# ---------------------------------------------------------------------------

class TestDescriptionPoints:
    def test_empty_text_gives_no_points(self):
        assert description_points("") == []
        assert description_points(None) == []
        assert description_points("   \n  ") == []

    def test_paragraph_split_into_sentences(self):
        text = "We build APIs. You will use FastAPI. Docker is a plus."
        assert description_points(text) == ["We build APIs.", "You will use FastAPI.", "Docker is a plus."]

    def test_bullet_lines_become_points(self):
        text = "Responsibilities:\n- Build APIs\n• Write tests\n* Deploy with Docker"
        assert description_points(text) == ["Responsibilities:", "Build APIs", "Write tests", "Deploy with Docker"]

    def test_numbered_lines_become_points(self):
        assert description_points("1. Design systems\n2) Review code") == ["Design systems", "Review code"]

    def test_does_not_split_inside_numbers_or_abbreviations(self):
        # "10K+" and "3.5" must not start a new point.
        pts = description_points("Handle 10K+ requests. Need 3.5 years in Python.")
        assert pts == ["Handle 10K+ requests.", "Need 3.5 years in Python."]

    def test_single_line_without_punctuation(self):
        assert description_points("Need a ML engineer") == ["Need a ML engineer"]


# ---------------------------------------------------------------------------
# Save / unsave + profile
# ---------------------------------------------------------------------------

JDS = [
    {"jd_id": "jd_a", "role_title": "Backend Dev", "company": "Acme", "location": "Pune",
     "required_skills": ["Python"], "preferred_skills": [], "experience_years": 2,
     "jd_text": "Build APIs. Write tests. Deploy services. Mentor juniors.", "is_active": True},
    {"jd_id": "jd_b", "role_title": "Old Role", "company": "Gone Inc", "location": "Remote",
     "required_skills": ["Java"], "preferred_skills": [], "experience_years": 5,
     "jd_text": "", "is_active": False},
]


@pytest.fixture()
def cand_client(tmp_path, monkeypatch):
    """Logged-in candidate with a parsed resume; all data in tmp_path, no ML."""
    import auth
    import routes.candidate as cand_routes

    monkeypatch.setattr(auth, "CANDIDATES_FILE", tmp_path / "candidates.json")
    monkeypatch.setattr(auth, "ADMINS_FILE", tmp_path / "admins.json")

    jds_dir = tmp_path / "jds"
    jds_dir.mkdir()
    (jds_dir / "jds.json").write_text(json.dumps(JDS))
    monkeypatch.setattr(cand_routes, "JDS_DIR", jds_dir)

    parsed_dir = tmp_path / "parsed"
    parsed_dir.mkdir()
    monkeypatch.setattr(cand_routes, "PARSED_RESUMES_DIR", parsed_dir)
    monkeypatch.setattr(cand_routes, "semantic_score", lambda *args, **kwargs: None)

    import main
    app = main.create_app()
    app.config.update(TESTING=True)
    client = app.test_client()

    client.post("/register", data={"name": "Cand", "email": "c@x.com", "password": "secret1",
                                   "role": "candidate", "target_role": "Backend Developer"})
    cid = auth.load_json(auth.CANDIDATES_FILE)[0]["candidate_id"]
    (parsed_dir / f"{cid}.json").write_text(json.dumps(
        {"raw_text": "Python dev", "skills": ["Python"], "experience_years": 3, "education": "B.Tech"}))
    client.cid = cid
    return client


def _saved(client):
    import auth
    return [s["jd_id"] for s in auth.get_candidate(client.cid).get("saved_jobs", [])]


class TestSaveJob:
    def test_save_via_fetch_returns_json_and_persists(self, cand_client):
        r = cand_client.post("/candidate/jobs/jd_a/save", headers={"X-Requested-With": "fetch"})
        assert r.status_code == 200
        assert r.get_json() == {"saved": True, "count": 1}
        assert _saved(cand_client) == ["jd_a"]

    def test_second_click_unsaves(self, cand_client):
        cand_client.post("/candidate/jobs/jd_a/save", headers={"X-Requested-With": "fetch"})
        r = cand_client.post("/candidate/jobs/jd_a/save", headers={"X-Requested-With": "fetch"})
        assert r.get_json() == {"saved": False, "count": 0}
        assert _saved(cand_client) == []

    def test_saved_at_timestamp_recorded(self, cand_client):
        import auth
        cand_client.post("/candidate/jobs/jd_a/save", headers={"X-Requested-With": "fetch"})
        entry = auth.get_candidate(cand_client.cid)["saved_jobs"][0]
        assert entry["saved_at"][:2] == "20"

    def test_newest_save_first(self, cand_client):
        for jd in ("jd_a", "jd_b"):
            cand_client.post(f"/candidate/jobs/{jd}/save", headers={"X-Requested-With": "fetch"})
        assert _saved(cand_client) == ["jd_b", "jd_a"]

    def test_unknown_job_404_json(self, cand_client):
        r = cand_client.post("/candidate/jobs/nope/save", headers={"X-Requested-With": "fetch"})
        assert r.status_code == 404
        assert _saved(cand_client) == []

    def test_form_post_redirects_to_next(self, cand_client):
        r = cand_client.post("/candidate/jobs/jd_a/save", data={"next": "/candidate/profile"})
        assert r.status_code == 302
        assert r.headers["Location"].endswith("/candidate/profile")

    def test_form_post_ignores_external_next(self, cand_client):
        r = cand_client.post("/candidate/jobs/jd_a/save", data={"next": "https://evil.example"})
        assert r.headers["Location"].endswith("/candidate/match-jobs")

    def test_requires_login(self, cand_client):
        cand_client.get("/logout")
        r = cand_client.post("/candidate/jobs/jd_a/save")
        assert r.status_code == 302
        assert "/login" in r.headers["Location"]
        assert _saved(cand_client) == []


class TestJobCardPage:
    def test_card_shows_meta_points_toggle_and_buttons(self, cand_client):
        html = cand_client.get("/candidate/match-jobs").get_data(as_text=True)
        assert "Acme" in html and "Pune" in html and "2+ yrs experience" in html
        assert " | " not in html.split("job-meta", 1)[1].split("</div>", 1)[0]  # no pipes
        assert "Build APIs." in html
        assert "Show 2 more" in html            # 4 points, 2 shown up front
        assert 'class="btn btn-primary btn-apply"' in html
        assert "btn-save" in html

    def test_inactive_jobs_not_listed(self, cand_client):
        html = cand_client.get("/candidate/match-jobs").get_data(as_text=True)
        assert "Old Role" not in html

    def test_saved_state_rendered(self, cand_client):
        cand_client.post("/candidate/jobs/jd_a/save", headers={"X-Requested-With": "fetch"})
        html = cand_client.get("/candidate/match-jobs").get_data(as_text=True)
        assert 'aria-pressed="true"' in html
        assert ">Saved<" in html


class TestProfileSavedJobs:
    def test_empty_state(self, cand_client):
        html = cand_client.get("/candidate/profile").get_data(as_text=True)
        assert "Saved Jobs" in html
        assert "haven't saved any jobs yet" in html

    def test_lists_saved_jobs_and_marks_closed(self, cand_client):
        for jd in ("jd_a", "jd_b"):
            cand_client.post(f"/candidate/jobs/{jd}/save", headers={"X-Requested-With": "fetch"})
        html = cand_client.get("/candidate/profile").get_data(as_text=True)
        assert "Backend Dev" in html and "Old Role" in html
        assert "No longer open" in html
        saved_section = html.split('id="saved-jobs"', 1)[1]
        assert saved_section.index("Old Role") < saved_section.index("Backend Dev")  # newest first

    def test_remove_from_profile(self, cand_client):
        cand_client.post("/candidate/jobs/jd_a/save", headers={"X-Requested-With": "fetch"})
        r = cand_client.post("/candidate/jobs/jd_a/save", data={"next": "/candidate/profile#saved-jobs"},
                             follow_redirects=True)
        assert "Job removed from saved jobs." in r.get_data(as_text=True)
        assert _saved(cand_client) == []
