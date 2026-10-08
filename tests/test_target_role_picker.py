"""Find Jobs target-role dropdown: roles split from registration, one scored at a time."""
import json
import re
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest


def _jd(jd_id, title, location="Remote"):
    # Identical apart from the title, so the role score decides the order.
    return {"jd_id": jd_id, "role_title": title, "company": "Acme", "location": location,
            "experience_years": 2, "required_skills": ["Python", "SQL"], "preferred_skills": [],
            "jd_text": f"{title} role.", "is_active": True, "created_at": "2026-09-01T10:00:00"}


JDS = [_jd("jd_ml", "Machine Learning Engineer"),
       _jd("jd_da", "Data Analyst", location="Pune"),
       _jd("jd_chef", "Chef")]


def _client(tmp_path, monkeypatch, target_role):
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
    monkeypatch.setattr(cand_routes, "semantic_score", lambda *a, **k: None)

    import main
    app = main.create_app()
    app.config.update(TESTING=True)
    client = app.test_client()
    client.post("/register", data={"name": "C", "email": "c@x.com", "password": "secret1",
                                   "role": "candidate", "target_role": target_role})
    cid = auth.load_json(auth.CANDIDATES_FILE)[0]["candidate_id"]
    (parsed_dir / f"{cid}.json").write_text(json.dumps(
        {"raw_text": "x", "skills": ["Python", "SQL"], "experience_years": 3, "education": "B.Tech"}))
    return client


@pytest.fixture()
def two_roles(tmp_path, monkeypatch):
    return _client(tmp_path, monkeypatch, "Data Analyst, ML Engineer")


def _titles(html):
    """Job titles in the order the cards appear."""
    return re.findall(r'<h3 class="job-title">([^<]+)</h3>', html)


def _page(client, url="/candidate/match-jobs"):
    return client.get(url).get_data(as_text=True)


class TestDropdown:
    def test_lists_roles_in_typed_order_first_selected(self, two_roles):
        html = _page(two_roles)
        options = re.findall(r'<input type="radio" name="role" value="([^"]+)"\s*(checked)?\s*>', html)
        assert options == [("Data Analyst", "checked"), ("ML Engineer", "")]
        # The pill shows the selected role.
        assert re.search(r'<summary class="is-active">Data Analyst ', html)

    def test_default_role_is_first(self, two_roles):
        assert _titles(_page(two_roles))[0] == "Data Analyst"

    def test_switching_role_rematches(self, two_roles):
        html = _page(two_roles, "/candidate/match-jobs?role=ML+Engineer")
        assert _titles(html)[0] == "Machine Learning Engineer"
        assert '<input type="radio" name="role" value="ML Engineer" checked>' in html

    def test_role_param_is_case_insensitive(self, two_roles):
        assert _titles(_page(two_roles, "/candidate/match-jobs?role=ml+engineer"))[0] == "Machine Learning Engineer"

    def test_unknown_role_falls_back_to_first(self, two_roles):
        html = _page(two_roles, "/candidate/match-jobs?role=Astronaut")
        assert _titles(html)[0] == "Data Analyst"
        assert '<input type="radio" name="role" value="Data Analyst" checked>' in html

    def test_scores_change_with_role(self, two_roles):
        def overall(html, title):
            card = html.split(f'<h3 class="job-title">{title}</h3>')[1]
            return float(re.search(r"([\d.]+)% match", card).group(1))
        da = _page(two_roles)
        ml = _page(two_roles, "/candidate/match-jobs?role=ML+Engineer")
        assert overall(da, "Data Analyst") > overall(ml, "Data Analyst")
        assert overall(ml, "Machine Learning Engineer") > overall(da, "Machine Learning Engineer")

    def test_single_role_shows_no_dropdown(self, tmp_path, monkeypatch):
        html = _page(_client(tmp_path, monkeypatch, "Data Analyst"))
        assert 'name="role"' not in html
        assert '<span class="role-single">Data Analyst</span>' in html

    def test_no_role_shows_add_prompt(self, tmp_path, monkeypatch):
        # Registration requires a target role, so register with one and then
        # clear it from the Profile page (which allows an empty value).
        client = _client(tmp_path, monkeypatch, "Data Analyst")
        client.post("/candidate/target-roles", data={"target_role": ""})
        html = _page(client)
        assert 'name="role"' not in html
        assert "Add target role</a>" in html


class TestRoleIsKept:
    def test_filters_keep_role(self, two_roles):
        html = _page(two_roles, "/candidate/match-jobs?role=ML+Engineer&loc=pune")
        assert _titles(html) == ["Data Analyst"]                      # filter still applies
        chip = re.search(r'class="filter-chip" href="([^"]+)"', html).group(1)
        assert "role=ML+Engineer" in chip and "loc=" not in chip
        clear = re.search(r'class="filter-clear" href="([^"]+)"', html).group(1)
        assert clear == "/candidate/match-jobs?role=ML+Engineer"

    def test_detail_link_carries_role(self, two_roles):
        html = _page(two_roles, "/candidate/match-jobs?role=ML+Engineer")
        assert "/candidate/match-jobs/jd_ml?role=ML+Engineer" in html

    def test_detail_scores_for_selected_role(self, two_roles):
        def role_score(html):
            return float(re.search(r"Role Relevance.*?</td>\s*<td[^>]*>([\d.]+)%", html, re.S).group(1))
        default = _page(two_roles, "/candidate/match-jobs/jd_ml")
        chosen = _page(two_roles, "/candidate/match-jobs/jd_ml?role=ML+Engineer")
        assert role_score(default) == 20.0          # Data Analyst vs ML Engineer
        assert role_score(chosen) == 100.0
        assert "Scored for target role: <strong" in chosen and "ML Engineer</strong>" in chosen
        assert 'href="/candidate/match-jobs?role=ML+Engineer"' in chosen   # back link

    def test_save_redirect_returns_to_same_role(self, two_roles):
        r = two_roles.post("/candidate/jobs/jd_ml/save",
                           data={"next": "/candidate/match-jobs?role=ML+Engineer"})
        assert r.headers["Location"].endswith("/candidate/match-jobs?role=ML+Engineer")


class TestAdminRankingUsesBestRole:
    def test_pipeline_scores_best_of_roles(self, monkeypatch):
        import pipeline.matcher as pm
        monkeypatch.setattr(pm, "semantic_score", lambda *a, **k: None)
        monkeypatch.setattr(pm, "explain_match", lambda *a, **k: None)
        cands = [{"candidate_id": "c1", "target_role": "Data Analyst, ML Engineer"}]
        parsed = {"skills": ["Python"], "experience_years": 3, "raw_text": "x"}
        for title in ("Machine Learning Engineer", "Data Analyst"):
            ranked = pm.two_stage_match(_jd("j", title), cands, lambda cid: parsed)
            assert ranked[0]["result"]["role_score"] == 100.0


@pytest.fixture()
def admin_page(tmp_path, monkeypatch):
    """Admin client + a helper that ranks candidates (given target roles) for a JD title."""
    import auth
    import routes.admin as admin_routes
    import pipeline.matcher as pm
    from config import ADMIN_KEY

    monkeypatch.setattr(auth, "CANDIDATES_FILE", tmp_path / "candidates.json")
    monkeypatch.setattr(auth, "ADMINS_FILE", tmp_path / "admins.json")
    monkeypatch.setattr(admin_routes, "JDS_FILE", tmp_path / "jds.json")
    parsed_dir = tmp_path / "parsed"
    parsed_dir.mkdir()
    monkeypatch.setattr(admin_routes, "PARSED_RESUMES_DIR", parsed_dir)
    monkeypatch.setattr(pm, "semantic_score", lambda *a, **k: None)
    monkeypatch.setattr(pm, "explain_match", lambda *a, **k: None)

    import main
    app = main.create_app()
    app.config.update(TESTING=True)
    client = app.test_client()
    client.post("/register", data={"name": "A", "email": "a@x.com", "password": "secret1",
                                   "role": "admin", "admin_key": ADMIN_KEY})

    def rank(jd_title, target_roles):
        (tmp_path / "jds.json").write_text(json.dumps([_jd("jd_x", jd_title)]))
        cands = []
        for i, roles in enumerate(target_roles):
            cid = f"cand_{i}"
            cands.append({"candidate_id": cid, "name": f"Cand {i}", "email": f"{i}@x.com",
                          "password_hash": "x", "target_role": roles, "resume_uploaded": True})
            (parsed_dir / f"{cid}.json").write_text(json.dumps(
                {"raw_text": "x", "skills": ["Python"], "experience_years": 3, "education": "B.Tech"}))
        (tmp_path / "candidates.json").write_text(json.dumps(cands))
        return client.get("/admin/candidates-for-jd/jd_x").get_data(as_text=True)

    return rank


def _best_role_line(html):
    m = re.search(r'<p class="best-role">(.*?)</p>', html, re.S)
    return " ".join(re.sub(r"<[^>]+>", " ", m.group(1)).split()) if m else None


class TestAdminBestRoleLine:
    def test_shows_winning_role(self, admin_page):
        html = admin_page("Machine Learning Engineer", ["Data Analyst, ML Engineer"])
        assert _best_role_line(html) == "Best role match: ML Engineer with 100.0% role fit"

    def test_other_job_other_role(self, admin_page):
        html = admin_page("Data Analyst", ["Data Analyst, ML Engineer"])
        assert _best_role_line(html) == "Best role match: Data Analyst with 100.0% role fit"

    def test_tie_lists_both(self, admin_page):
        html = admin_page("Data Engineer", ["Data Analyst, ML Engineer"])
        assert _best_role_line(html) == "Best role match: Data Analyst ML Engineer tied at 66.7% role fit"
        assert html.count('class="best-role-tag"') == 2

    def test_no_role_matches(self, admin_page):
        html = admin_page("Chef", ["Data Analyst, ML Engineer"])
        assert _best_role_line(html) == "None of this candidate's target roles match the job title."

    def test_single_role_candidate_has_no_line(self, admin_page):
        html = admin_page("Machine Learning Engineer", ["ML Engineer"])
        assert _best_role_line(html) is None
        assert "Target: ML Engineer" in html
