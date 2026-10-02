"""Integration tests for the Flask routes via the test client.

Covers the public auth lifecycle (register → dashboard → logout → login) and
the admin JD lifecycle (upload → deactivate → reactivate). User data is
isolated to a tmp dir so the real data/*.json files are never touched.
"""
import io
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest

import auth
import resume_parser
import routes.candidate as cand_routes
from storage import s3 as s3_storage


@pytest.fixture()
def isolated_client(tmp_path, monkeypatch):
    """A Flask test client whose auth + JD data live in a tmp dir."""
    # Redirect the JSON stores BEFORE the app imports/uses them.
    import auth
    from config import ADMIN_KEY
    cand_file = tmp_path / "candidates.json"
    admin_file = tmp_path / "admins.json"
    monkeypatch.setattr(auth, "CANDIDATES_FILE", cand_file)
    monkeypatch.setattr(auth, "ADMINS_FILE", admin_file)

    import routes.admin as admin_routes
    jds_file = tmp_path / "jds.json"
    monkeypatch.setattr(admin_routes, "JDS_FILE", jds_file)

    import main
    app = main.create_app()
    app.config.update(TESTING=True)
    return app.test_client(), ADMIN_KEY, jds_file


class TestHealth:
    def test_health_no_auth(self, isolated_client):
        client, _, _ = isolated_client
        resp = client.get("/health")
        assert resp.status_code == 200
        assert resp.get_json()["status"] == "healthy"


class TestAuthLifecycle:
    def test_register_candidate_then_dashboard(self, isolated_client):
        client, _, _ = isolated_client
        resp = client.post("/register", data={
            "name": "Alice", "email": "alice@x.com", "password": "secret1",
            "target_role": "Backend Dev", "role": "candidate",
        }, follow_redirects=False)
        # Candidates without a resume are sent directly to targeted role suggestions.
        assert resp.status_code == 302
        assert "/candidate/match-jobs" in resp.headers["Location"]

    def test_register_rejects_short_password(self, isolated_client):
        client, _, _ = isolated_client
        resp = client.post("/register", data={
            "name": "Bob", "email": "bob@x.com", "password": "123",
            "role": "candidate",
        })
        assert resp.status_code == 200  # re-renders form, no redirect
        assert b"at least 6" in resp.data

    def test_login_after_register(self, isolated_client):
        client, _, _ = isolated_client
        client.post("/register", data={
            "name": "Cara", "email": "cara@x.com", "password": "secret1",
            "target_role": "Dev", "role": "candidate",
        })
        client.get("/logout")
        resp = client.post("/login", data={"email": "cara@x.com", "password": "secret1"},
                           follow_redirects=False)
        assert resp.status_code == 302
        assert "/dashboard" in resp.headers["Location"]

    def test_login_wrong_password(self, isolated_client):
        client, _, _ = isolated_client
        client.post("/register", data={
            "name": "Dan", "email": "dan@x.com", "password": "secret1",
            "target_role": "Dev", "role": "candidate",
        })
        client.get("/logout")
        resp = client.post("/login", data={"email": "dan@x.com", "password": "wrong"})
        assert resp.status_code == 200
        assert b"Invalid email or password" in resp.data


class TestCandidateSuggestionsAndResumes:
    def test_register_candidate_without_resume_redirects_to_role_suggestions(self, isolated_client):
        client, _, _ = isolated_client
        resp = client.post("/register", data={
            "name": "Eve", "email": "eve@x.com", "password": "secret1",
            "target_role": "Backend Engineer", "role": "candidate",
        }, follow_redirects=False)
        assert resp.status_code == 302
        assert "/candidate/match-jobs" in resp.headers["Location"]

    def test_target_role_is_saved(self, isolated_client):
        client, _, _ = isolated_client
        client.post("/register", data={
            "name": "Frank", "email": "frank@x.com", "password": "secret1",
            "target_role": "Data Engineer", "role": "candidate",
        }, follow_redirects=False)
        stored = auth.load_json(auth.CANDIDATES_FILE)
        assert stored[0]["target_role"] == "Data Engineer"

    def test_role_suggestions_page_shows_jobs_without_scores(self, isolated_client):
        client, _, jds_file = isolated_client
        jds_file.write_text(json.dumps([{
            "jd_id": "jd_1", "role_title": "Backend Engineer",
            "company": "Acme", "required_skills": ["Python"],
            "preferred_skills": ["SQL"], "experience_years": 3,
            "jd_text": "Build backend services.", "location": "Remote",
            "is_active": True,
        }]))

        client.post("/register", data={
            "name": "Grace", "email": "grace@x.com", "password": "secret1",
            "target_role": "Backend Engineer", "role": "candidate",
        }, follow_redirects=False)

        resp = client.get("/candidate/match-jobs")
        assert resp.status_code == 200
        assert b"Role Suggestions" in resp.data
        assert b"% match" not in resp.data
        assert b"Skill match" not in resp.data

    def test_no_resume_skips_parsing_and_embedding(self, isolated_client, monkeypatch):
        client, _, _ = isolated_client
        calls = {"parse": 0, "index": 0}

        def fake_parse(raw_text):
            calls["parse"] += 1
            return {"skills": ["Python"], "experience_years": 3, "education": "B.Sc.", "raw_text": raw_text}

        def fake_index(candidate_id, text):
            calls["index"] += 1

        monkeypatch.setattr(cand_routes, "parse_resume_smart", fake_parse)
        monkeypatch.setattr(cand_routes, "index_resume", fake_index)

        client.post("/register", data={
            "name": "Hank", "email": "hank@x.com", "password": "secret1",
            "target_role": "ML Engineer", "role": "candidate",
        }, follow_redirects=False)

        assert calls == {"parse": 0, "index": 0}

    def test_resume_based_matching_still_works(self, isolated_client, monkeypatch):
        client, _, jds_file = isolated_client
        jds_file.write_text(json.dumps([{
            "jd_id": "jd_1", "role_title": "Backend Engineer",
            "company": "Acme", "required_skills": ["Python"],
            "preferred_skills": ["SQL"], "experience_years": 3,
            "jd_text": "Build backend services.", "location": "Remote",
            "is_active": True,
        }]))

        client.post("/register", data={
            "name": "Iris", "email": "iris@x.com", "password": "secret1",
            "target_role": "Backend Engineer", "role": "candidate",
        }, follow_redirects=False)

        def fake_parse(raw_text):
            return {
                "skills": ["Python"],
                "experience_years": 3,
                "education": "B.Sc.",
                "raw_text": raw_text,
            }

        monkeypatch.setattr(s3_storage, "upload_file", lambda *args, **kwargs: "resumes/test/resume.txt")
        monkeypatch.setattr(s3_storage, "download_file", lambda *args, **kwargs: b"Python backend engineer resume")
        monkeypatch.setattr(resume_parser, "extract_text_from_bytes", lambda *args, **kwargs: "Python backend engineer resume")
        monkeypatch.setattr(cand_routes, "parse_resume_smart", fake_parse)
        monkeypatch.setattr(cand_routes, "index_resume", lambda *args, **kwargs: None)

        resp = client.post("/candidate/upload-resume", data={
            "resume": (io.BytesIO(b"Python backend engineer resume"), "resume.txt")
        }, content_type="multipart/form-data", follow_redirects=False)
        assert resp.status_code == 302

        resp = client.get("/candidate/match-jobs")
        assert resp.status_code == 200
        assert b"Your Job Matches" in resp.data
        assert b"% match" in resp.data


class TestAdminJdLifecycle:
    def _login_admin(self, client, admin_key):
        client.post("/register", data={
            "name": "Admin", "email": "admin@x.com", "password": "secret1",
            "role": "admin", "admin_key": admin_key,
        })

    def test_upload_and_toggle_jd(self, isolated_client):
        client, admin_key, jds_file = isolated_client
        self._login_admin(client, admin_key)

        # Upload a JD.
        resp = client.post("/admin/upload-jd", data={
            "role_title": "Backend Engineer", "company": "Acme",
            "required_skills": "Python, SQL", "preferred_skills": "AWS",
            "experience_years": "3", "jd_text": "Build APIs.", "location": "Remote",
        }, follow_redirects=False)
        assert resp.status_code == 302

        import json
        jds = json.loads(jds_file.read_text())
        assert len(jds) == 1
        jd_id = jds[0]["jd_id"]
        assert jds[0]["is_active"] is True

        # Deactivate it.
        client.post(f"/admin/jds/{jd_id}/delete")
        jds = json.loads(jds_file.read_text())
        assert jds[0]["is_active"] is False

        # Reactivate it.
        client.post(f"/admin/jds/{jd_id}/reactivate")
        jds = json.loads(jds_file.read_text())
        assert jds[0]["is_active"] is True

    def test_admin_route_denied_for_anonymous(self, isolated_client):
        client, _, _ = isolated_client
        resp = client.get("/admin/dashboard", follow_redirects=False)
        # Not logged in → redirect (login_required).
        assert resp.status_code == 302
