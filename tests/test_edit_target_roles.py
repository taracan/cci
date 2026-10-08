"""Candidates can change their target role(s) any time from the Profile page."""
import json
import re
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest


@pytest.fixture()
def cand(tmp_path, monkeypatch):
    """Logged-in candidate registered as 'Data Analyst' with a parsed resume + 2 JDs."""
    import auth
    import routes.candidate as cand_routes

    monkeypatch.setattr(auth, "CANDIDATES_FILE", tmp_path / "candidates.json")
    monkeypatch.setattr(auth, "ADMINS_FILE", tmp_path / "admins.json")
    jds_dir = tmp_path / "jds"
    jds_dir.mkdir()
    jd = lambda i, t: {"jd_id": i, "role_title": t, "company": "Acme", "location": "Remote",  # noqa: E731
                       "experience_years": 1, "required_skills": ["Python"], "jd_text": f"{t}.",
                       "is_active": True, "created_at": "2026-09-01T10:00:00"}
    (jds_dir / "jds.json").write_text(json.dumps([jd("jd_da", "Data Analyst"), jd("jd_ml", "ML Engineer")]))
    monkeypatch.setattr(cand_routes, "JDS_DIR", jds_dir)
    parsed = tmp_path / "parsed"
    parsed.mkdir()
    monkeypatch.setattr(cand_routes, "PARSED_RESUMES_DIR", parsed)
    monkeypatch.setattr(cand_routes, "semantic_score", lambda *a, **k: None)

    import main
    app = main.create_app()
    app.config.update(TESTING=True)
    client = app.test_client()
    client.post("/register", data={"name": "C", "email": "c@x.com", "password": "secret1",
                                   "role": "candidate", "target_role": "Data Analyst"})
    cid = auth.load_json(auth.CANDIDATES_FILE)[0]["candidate_id"]
    (parsed / f"{cid}.json").write_text(json.dumps(
        {"raw_text": "x", "skills": ["Python"], "experience_years": 2, "education": "B.Tech"}))
    client.stored_role = lambda: auth.get_candidate(cid)["target_role"]
    yield client
    # create_app() seeds the shared in-memory DB; clear it so later tests start clean.
    import db.database as dbmod
    dbmod.Base.metadata.drop_all(bind=dbmod.engine)


def _save(client, value, **extra):
    return client.post("/candidate/target-roles", data={"target_role": value, **extra})


class TestUpdateRoute:
    def test_change_role(self, cand):
        r = _save(cand, "ML Engineer")
        assert r.status_code == 302 and r.headers["Location"].endswith("/candidate/profile#target-roles")
        assert cand.stored_role() == "ML Engineer"

    def test_multiple_roles_are_tidied(self, cand):
        _save(cand, "  ML Engineer;data analyst |  Data Analyst or AI/ML Engineer ,, ")
        assert cand.stored_role() == "ML Engineer, data analyst, AI/ML Engineer"

    def test_clear_role(self, cand):
        _save(cand, "   ")
        assert cand.stored_role() == ""
        html = cand.get("/candidate/profile").get_data(as_text=True)
        assert "Add target role" in html and "Target role cleared" in html

    def test_success_message(self, cand):
        html = cand.post("/candidate/target-roles", data={"target_role": "A, B"},
                         follow_redirects=True).get_data(as_text=True)
        assert "Target roles updated: A, B." in html

    def test_too_long_rejected_and_unchanged(self, cand):
        r = _save(cand, "x" * 201, next="/candidate/match-jobs")
        assert cand.stored_role() == "Data Analyst"
        assert "edit=roles" in r.headers["Location"] and "next=/candidate/match-jobs" in r.headers["Location"]

    def test_returns_to_next(self, cand):
        r = _save(cand, "ML Engineer", next="/candidate/match-jobs")
        assert r.headers["Location"].endswith("/candidate/match-jobs")

    @pytest.mark.parametrize("bad", ["https://evil.example", "//evil.example", "javascript:alert(1)"])
    def test_external_next_ignored(self, cand, bad):
        r = _save(cand, "ML Engineer", next=bad)
        assert r.headers["Location"].endswith("/candidate/profile#target-roles")

    def test_requires_candidate_login(self, cand):
        cand.get("/logout")
        r = _save(cand, "Hacker")
        assert "/login" in r.headers["Location"]
        assert cand.stored_role() == "Data Analyst"

    def test_get_not_allowed(self, cand):
        assert cand.get("/candidate/target-roles").status_code == 405


class TestProfilePage:
    def test_shows_pills_and_prefilled_form(self, cand):
        _save(cand, "Data Analyst, ML Engineer")
        html = cand.get("/candidate/profile").get_data(as_text=True)
        assert re.findall(r'<span class="role-pill">([^<]+)</span>', html) == ["Data Analyst", "ML Engineer"]
        assert 'value="Data Analyst, ML Engineer"' in html
        assert "Target Roles</td>" in html

    def test_edit_link_opens_form_and_keeps_next(self, cand):
        html = cand.get("/candidate/profile?edit=roles&next=/candidate/match-jobs").get_data(as_text=True)
        assert '<details class="role-edit" open>' in html
        assert '<input type="hidden" name="next" value="/candidate/match-jobs">' in html
        assert 'href="/candidate/match-jobs" class="btn btn-secondary">Cancel' in html

    def test_external_next_not_rendered(self, cand):
        html = cand.get("/candidate/profile?edit=roles&next=https://evil.example").get_data(as_text=True)
        assert 'name="next" value=""' in html and "evil.example" not in html


class TestMatchingUsesNewRoles:
    def _first_title(self, cand):
        html = cand.get("/candidate/match-jobs").get_data(as_text=True)
        return re.findall(r'<h3 class="job-title">([^<]+)</h3>', html)[0]

    def test_ranking_follows_change(self, cand):
        assert self._first_title(cand) == "Data Analyst"
        _save(cand, "ML Engineer, Data Analyst")
        assert self._first_title(cand) == "ML Engineer"       # new first role is the default

    def test_find_jobs_has_edit_link(self, cand):
        html = cand.get("/candidate/match-jobs").get_data(as_text=True)
        assert '/candidate/profile?edit=roles&amp;next=/candidate/match-jobs#target-roles">Edit roles' in html

    def test_find_jobs_prompts_when_no_role(self, cand):
        _save(cand, "")
        html = cand.get("/candidate/match-jobs").get_data(as_text=True)
        assert "Add target role</a>" in html and 'name="role"' not in html
