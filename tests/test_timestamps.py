"""Tests for timestamps: formatting helpers, what gets recorded, and where it shows."""
import io
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from timefmt import time_ago, iso_utc, parse_utc, utc_label, utc_now_iso  # noqa: E402

NOW = "2026-09-24T12:00:00"


class TestTimeAgo:
    @pytest.mark.parametrize("then, expected", [
        ("2026-09-24T11:59:30", "just now"),
        ("2026-09-24T11:59:00", "1 minute ago"),
        ("2026-09-24T11:15:00", "45 minutes ago"),
        ("2026-09-24T10:00:00", "2 hours ago"),
        ("2026-09-23T10:00:00", "yesterday"),
        ("2026-09-20T12:00:00", "4 days ago"),
        ("2026-09-10T12:00:00", "2 weeks ago"),
        ("2026-07-20T12:00:00", "2 months ago"),
        ("2024-09-01T12:00:00", "2 years ago"),
    ])
    def test_relative(self, then, expected):
        assert time_ago(then, now=NOW) == expected

    def test_future_is_just_now(self):
        assert time_ago("2026-09-25T12:00:00", now=NOW) == "just now"

    def test_blank_or_bad_values(self):
        assert time_ago("", now=NOW) == "" and time_ago(None, now=NOW) == ""
        assert time_ago("not a date", now=NOW) == ""

    def test_old_microsecond_format_still_parses(self):
        # Records written before this change use datetime.utcnow().isoformat()
        assert time_ago("2026-09-24T09:59:59.123456", now=NOW) == "2 hours ago"


class TestFormats:
    def test_iso_utc_adds_z(self):
        assert iso_utc("2026-09-24T21:40:12.5") == "2026-09-24T21:40:12Z"
        assert iso_utc("") == ""

    def test_z_suffix_input(self):
        assert parse_utc("2026-09-24T21:40:12Z") == parse_utc("2026-09-24T21:40:12")

    def test_utc_label(self):
        assert utc_label("2026-09-04T07:05:00") == "Sep 4, 2026, 07:05 UTC"
        assert utc_label("2026-09-04T07:05:00", with_time=False) == "Sep 4, 2026"

    def test_utc_now_iso_shape(self):
        s = utc_now_iso()
        assert len(s) == 19 and s[10] == "T" and "+" not in s


# ---------------------------------------------------------------------------
# What gets recorded + where it shows
# ---------------------------------------------------------------------------

JDS = [{"jd_id": "jd_t", "role_title": "Timed Role", "company": "Acme", "location": "Pune",
        "required_skills": ["Python"], "preferred_skills": [], "experience_years": 2,
        "jd_text": "Build.", "is_active": True, "uploaded_by": "", "created_at": "2026-01-01T09:00:00"}]


@pytest.fixture()
def env(tmp_path, monkeypatch):
    import auth
    import routes.candidate as cand_routes
    import routes.admin as admin_routes

    monkeypatch.setattr(auth, "CANDIDATES_FILE", tmp_path / "candidates.json")
    monkeypatch.setattr(auth, "ADMINS_FILE", tmp_path / "admins.json")
    monkeypatch.setattr(admin_routes, "ADMINS_FILE", tmp_path / "admins.json")
    jds_dir = tmp_path / "jds"
    jds_dir.mkdir()
    jds_file = jds_dir / "jds.json"
    jds_file.write_text(json.dumps(JDS))
    monkeypatch.setattr(cand_routes, "JDS_DIR", jds_dir)
    monkeypatch.setattr(admin_routes, "JDS_FILE", jds_file)
    parsed_dir = tmp_path / "parsed"
    parsed_dir.mkdir()
    monkeypatch.setattr(cand_routes, "PARSED_RESUMES_DIR", parsed_dir)
    monkeypatch.setattr(cand_routes, "semantic_score", lambda jd_text, cid: None)

    import main
    app = main.create_app()
    app.config.update(TESTING=True)
    return app.test_client(), jds_file, parsed_dir


def _jd(jds_file):
    return json.loads(jds_file.read_text())[0]


def _admin(client):
    import auth
    from config import ADMIN_KEY
    client.post("/register", data={"name": "Asha Admin", "email": "a@x.com", "password": "secret1",
                                   "role": "admin", "admin_key": ADMIN_KEY})
    return auth.load_json(auth.ADMINS_FILE)[0]["admin_id"]


class TestJdTimestamps:
    def test_new_jd_has_created_and_no_updated(self, env):
        client, jds_file, _ = env
        admin_id = _admin(client)
        client.post("/admin/upload-jd", data={"role_title": "New", "company": "Co", "required_skills": "Go",
                                              "experience_years": "1", "jd_text": "Do.", "location": "Remote"})
        new = [j for j in json.loads(jds_file.read_text()) if j["role_title"] == "New"][0]
        assert len(new["created_at"]) == 19 and new["uploaded_by"] == admin_id
        assert new["updated_at"] is None and new["updated_by"] is None

    def test_edit_records_updated_at_and_by(self, env):
        client, jds_file, _ = env
        admin_id = _admin(client)
        client.post("/admin/jds/jd_t/edit", data={"role_title": "Timed Role 2", "company": "Acme",
                                                  "required_skills": "Python", "experience_years": "2",
                                                  "location": "Pune", "jd_text": "Build."})
        jd = _jd(jds_file)
        assert jd["updated_at"] and jd["updated_by"] == admin_id
        assert jd["created_at"] == "2026-01-01T09:00:00"        # unchanged

    def test_deactivate_records_update(self, env):
        client, jds_file, _ = env
        admin_id = _admin(client)
        client.post("/admin/jds/jd_t/delete")
        jd = _jd(jds_file)
        assert jd["updated_at"] and jd["updated_by"] == admin_id

    def test_reactivate_reposts(self, env):
        client, jds_file, _ = env
        admin_id = _admin(client)
        client.post("/admin/jds/jd_t/delete")
        client.post("/admin/jds/jd_t/reactivate")
        jd = _jd(jds_file)
        assert jd["is_active"] is True
        assert jd["created_at"] > "2026-01-01T09:00:00"          # posted time reset to now
        assert jd["first_posted_at"] == "2026-01-01T09:00:00"    # original kept
        assert jd["reposted_by"] == admin_id
        assert jd["updated_at"] is None and jd["updated_by"] is None

    def test_second_repost_keeps_original_first_posted(self, env):
        client, jds_file, _ = env
        _admin(client)
        for _ in range(2):
            client.post("/admin/jds/jd_t/delete")
            client.post("/admin/jds/jd_t/reactivate")
        assert _jd(jds_file)["first_posted_at"] == "2026-01-01T09:00:00"

    def test_jd_detail_shows_repost_info(self, env):
        client, _, _ = env
        _admin(client)
        client.post("/admin/jds/jd_t/delete")
        client.post("/admin/jds/jd_t/reactivate")
        html = client.get("/admin/jds/jd_t").get_data(as_text=True)
        assert "reposted by Asha Admin" in html and "First posted" in html
        assert "Not changed since posting" in html

    def test_jd_detail_shows_posted_updated_and_who(self, env):
        client, jds_file, _ = env
        _admin(client)
        html = client.get("/admin/jds/jd_t").get_data(as_text=True)
        assert 'data-local="datetime"' in html and "Not changed since posting" in html
        client.post("/admin/jds/jd_t/delete")
        html = client.get("/admin/jds/jd_t").get_data(as_text=True)
        assert "Last updated" in html and "by Asha Admin" in html

    def test_admin_lists_show_posted(self, env):
        client, _, _ = env
        _admin(client)
        assert "Posted" in client.get("/admin/dashboard").get_data(as_text=True)
        jd_list = client.get("/admin/jds").get_data(as_text=True)
        assert "Posted " in jd_list and 'data-local="ago"' in jd_list
        assert "Acme | Pune" not in jd_list                     # pipes gone here too


class TestCandidateTimestamps:
    def _candidate(self, client, parsed_dir):
        import auth
        client.post("/register", data={"name": "C", "email": "c@x.com", "password": "secret1",
                                       "role": "candidate", "target_role": "Dev"})
        cid = auth.load_json(auth.CANDIDATES_FILE)[0]["candidate_id"]
        (parsed_dir / f"{cid}.json").write_text(json.dumps(
            {"raw_text": "x", "skills": ["Python"], "experience_years": 3, "education": "B.Tech"}))
        return cid

    def test_card_and_detail_show_posted(self, env):
        client, _, parsed = env
        self._candidate(client, parsed)
        card = client.get("/candidate/match-jobs").get_data(as_text=True)
        assert 'datetime="2026-01-01T09:00:00Z"' in card and "Posted " in card
        detail = client.get("/candidate/match-jobs/jd_t").get_data(as_text=True)
        assert "Posted " in detail

    def test_card_shows_updated_when_jd_changed(self, env):
        client, jds_file, parsed = env
        self._candidate(client, parsed)
        jds = json.loads(jds_file.read_text())
        jds[0]["updated_at"] = "2026-02-01T10:00:00"
        jds_file.write_text(json.dumps(jds))
        card = client.get("/candidate/match-jobs").get_data(as_text=True)
        assert "Posted " in card and "Updated " in card
        assert 'datetime="2026-02-01T10:00:00Z"' in card

    def test_card_has_no_updated_when_never_changed(self, env):
        client, _, parsed = env
        self._candidate(client, parsed)
        assert "Updated " not in client.get("/candidate/match-jobs").get_data(as_text=True)

    def test_resume_upload_records_time(self, env, monkeypatch):
        import auth
        import routes.candidate as cand_routes
        client, _, parsed = env
        cid = self._candidate(client, parsed)
        monkeypatch.setattr(cand_routes, "PARSED_RESUMES_DIR", parsed)
        monkeypatch.setattr(cand_routes.s3_storage, "upload_file", lambda *a, **k: None)
        monkeypatch.setattr(cand_routes.s3_storage, "download_file", lambda key: b"Python dev")
        monkeypatch.setattr(cand_routes, "parse_resume_smart", lambda text: {
            "raw_text": text, "skills": ["Python"], "experience_years": 2, "education": "B.Tech", "summary": ""})
        monkeypatch.setattr(cand_routes, "index_resume", lambda cid, text: None)

        client.post("/candidate/upload-resume", data={"resume": (io.BytesIO(b"Python dev"), "cv.txt")},
                    content_type="multipart/form-data")
        assert len(auth.get_candidate(cid)["resume_uploaded_at"]) == 19
        assert "just now" in client.get("/candidate/dashboard").get_data(as_text=True)
        assert "just now" in client.get("/candidate/profile").get_data(as_text=True)
