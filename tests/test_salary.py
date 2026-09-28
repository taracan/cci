"""Tests for salary: formatting, admin form parsing, and where it shows up."""
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from salary import format_salary, parse_salary_form, EMPTY_SALARY  # noqa: E402


class TestFormatSalary:
    def test_missing_fields_is_none(self):
        assert format_salary({}) is None                       # old JD, no keys at all
        assert format_salary(dict(EMPTY_SALARY)) is None       # explicit nulls

    def test_inr_range_uses_lakhs(self):
        jd = {"salary_min": 1200000, "salary_max": 1800000, "salary_currency": "INR", "salary_period": "year"}
        assert format_salary(jd) == "₹12L – ₹18L / yr"

    def test_inr_crore_and_decimal(self):
        jd = {"salary_min": 850000, "salary_max": 12500000, "salary_currency": "INR", "salary_period": "year"}
        assert format_salary(jd) == "₹8.5L – ₹1.2Cr / yr"

    def test_usd_uses_k(self):
        jd = {"salary_min": 120000, "salary_max": 150000, "salary_currency": "USD", "salary_period": "year"}
        assert format_salary(jd) == "$120K – $150K / yr"

    def test_monthly(self):
        jd = {"salary_min": 80000, "salary_max": 80000, "salary_currency": "INR", "salary_period": "month"}
        assert format_salary(jd) == "₹80K / mo"

    def test_only_min_or_only_max(self):
        assert format_salary({"salary_min": 1000000, "salary_currency": "INR"}) == "From ₹10L / yr"
        assert format_salary({"salary_max": 90000, "salary_currency": "USD"}) == "Up to $90K / yr"

    def test_defaults_when_currency_period_missing(self):
        assert format_salary({"salary_min": 500000, "salary_max": 700000}) == "₹5L – ₹7L / yr"


class TestParseSalaryForm:
    def test_blank_means_not_disclosed(self):
        fields, err = parse_salary_form({"salary_min": "", "salary_max": "  "})
        assert err is None and fields == EMPTY_SALARY

    def test_absent_fields_means_not_disclosed(self):
        assert parse_salary_form({}) == (EMPTY_SALARY, None)

    def test_valid_range_with_commas(self):
        fields, err = parse_salary_form({"salary_min": "12,00,000", "salary_max": "1800000",
                                         "salary_currency": "INR", "salary_period": "year"})
        assert err is None
        assert fields == {"salary_min": 1200000, "salary_max": 1800000,
                          "salary_currency": "INR", "salary_period": "year"}

    @pytest.mark.parametrize("form, msg", [
        ({"salary_min": "abc"}, "positive number"),
        ({"salary_min": "-5"}, "positive number"),
        ({"salary_min": "20", "salary_max": "10"}, "lower than minimum"),
        ({"salary_min": "10", "salary_currency": "XYZ"}, "valid salary currency"),
    ])
    def test_invalid_input(self, form, msg):
        fields, err = parse_salary_form(form)
        assert msg in err
        assert fields == EMPTY_SALARY


# ---------------------------------------------------------------------------
# Pages
# ---------------------------------------------------------------------------

JDS = [
    {"jd_id": "jd_pay", "role_title": "Paid Role", "company": "Acme", "location": "Pune",
     "required_skills": ["Python"], "preferred_skills": [], "experience_years": 2,
     "jd_text": "Build APIs.", "is_active": True, "created_at": "2026-09-01T00:00:00",
     "salary_min": 1200000, "salary_max": 1800000, "salary_currency": "INR", "salary_period": "year"},
    {"jd_id": "jd_old", "role_title": "Legacy Role", "company": "OldCo", "location": "Remote",
     "required_skills": ["Python"], "preferred_skills": [], "experience_years": 1,
     "jd_text": "Old JD without salary keys.", "is_active": True, "created_at": "2026-01-01T00:00:00"},
]


@pytest.fixture()
def app_env(tmp_path, monkeypatch):
    """Flask client with auth/JD data in tmp_path and no ML."""
    import auth
    import routes.candidate as cand_routes
    import routes.admin as admin_routes

    monkeypatch.setattr(auth, "CANDIDATES_FILE", tmp_path / "candidates.json")
    monkeypatch.setattr(auth, "ADMINS_FILE", tmp_path / "admins.json")
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


def _login_candidate(client, parsed_dir):
    import auth
    client.post("/register", data={"name": "C", "email": "c@x.com", "password": "secret1",
                                   "role": "candidate", "target_role": "Dev"})
    cid = auth.load_json(auth.CANDIDATES_FILE)[0]["candidate_id"]
    (parsed_dir / f"{cid}.json").write_text(json.dumps(
        {"raw_text": "x", "skills": ["Python"], "experience_years": 3, "education": "B.Tech"}))


def _login_admin(client):
    from config import ADMIN_KEY
    client.post("/register", data={"name": "A", "email": "a@x.com", "password": "secret1",
                                   "role": "admin", "admin_key": ADMIN_KEY})


class TestCandidatePages:
    def test_card_shows_salary_and_placeholder(self, app_env):
        client, _, parsed = app_env
        _login_candidate(client, parsed)
        html = client.get("/candidate/match-jobs").get_data(as_text=True)
        assert "₹12L – ₹18L / yr" in html
        assert "Salary not disclosed" in html            # old JD without salary keys

    def test_match_detail_has_icons_salary_no_pipes(self, app_env):
        client, _, parsed = app_env
        _login_candidate(client, parsed)
        html = client.get("/candidate/match-jobs/jd_pay").get_data(as_text=True)
        assert 'class="job-meta"' in html
        assert "₹12L – ₹18L / yr" in html
        assert "Acme | Pune" not in html and " | " not in html.split('class="job-meta"', 1)[1].split("</div>", 1)[0]

    def test_match_detail_placeholder_for_old_jd(self, app_env):
        client, _, parsed = app_env
        _login_candidate(client, parsed)
        html = client.get("/candidate/match-jobs/jd_old").get_data(as_text=True)
        assert "Salary not disclosed" in html


class TestAdminSalaryForms:
    BASE = {"role_title": "New Role", "company": "NewCo", "required_skills": "Python",
            "experience_years": "2", "jd_text": "Build things.", "location": "Remote"}

    def test_upload_with_salary_saves_numbers(self, app_env):
        client, jds_file, _ = app_env
        _login_admin(client)
        r = client.post("/admin/upload-jd", data={**self.BASE, "salary_min": "10,00,000", "salary_max": "1500000",
                                                  "salary_currency": "INR", "salary_period": "year"})
        assert r.status_code == 302
        new = [j for j in json.loads(jds_file.read_text()) if j["role_title"] == "New Role"][0]
        assert (new["salary_min"], new["salary_max"], new["salary_currency"]) == (1000000, 1500000, "INR")

    def test_upload_without_salary_stores_nulls(self, app_env):
        client, jds_file, _ = app_env
        _login_admin(client)
        client.post("/admin/upload-jd", data=self.BASE)
        new = [j for j in json.loads(jds_file.read_text()) if j["role_title"] == "New Role"][0]
        assert all(new[k] is None for k in EMPTY_SALARY)

    def test_upload_rejects_bad_salary(self, app_env):
        client, jds_file, _ = app_env
        _login_admin(client)
        r = client.post("/admin/upload-jd", data={**self.BASE, "salary_min": "20", "salary_max": "10"})
        assert "lower than minimum" in r.get_data(as_text=True)
        assert not any(j["role_title"] == "New Role" for j in json.loads(jds_file.read_text()))

    def test_edit_adds_salary_to_old_jd(self, app_env):
        client, jds_file, _ = app_env
        _login_admin(client)
        page = client.get("/admin/jds/jd_old/edit").get_data(as_text=True)
        assert 'name="salary_min"' in page
        client.post("/admin/jds/jd_old/edit", data={
            "role_title": "Legacy Role", "company": "OldCo", "required_skills": "Python",
            "experience_years": "1", "location": "Remote", "jd_text": "Old JD.",
            "salary_min": "90000", "salary_max": "120000", "salary_currency": "USD", "salary_period": "year"})
        jd = [j for j in json.loads(jds_file.read_text()) if j["jd_id"] == "jd_old"][0]
        assert (jd["salary_min"], jd["salary_max"], jd["salary_currency"]) == (90000, 120000, "USD")

    def test_edit_form_prefills_existing_salary(self, app_env):
        client, _, _ = app_env
        _login_admin(client)
        page = client.get("/admin/jds/jd_pay/edit").get_data(as_text=True)
        assert 'value="1200000"' in page and 'value="1800000"' in page

    def test_jd_detail_shows_salary(self, app_env):
        client, _, _ = app_env
        _login_admin(client)
        assert "₹12L – ₹18L / yr" in client.get("/admin/jds/jd_pay").get_data(as_text=True)
        assert "Not disclosed" in client.get("/admin/jds/jd_old").get_data(as_text=True)
