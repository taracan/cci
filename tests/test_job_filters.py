"""Tests for Find Jobs filters: location/experience/salary options, filtering, sorting."""
import json
import sys
from pathlib import Path

import pytest
from werkzeug.datastructures import MultiDict

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import job_filters as jf  # noqa: E402


def _jd(jd_id, location="Pune", exp=2, sal=None, created="2026-01-01", currency="INR", period="year"):
    jd = {"jd_id": jd_id, "role_title": jd_id, "company": "Co", "location": location,
          "experience_years": exp, "created_at": created, "required_skills": ["Python"],
          "preferred_skills": [], "jd_text": "Do things.", "is_active": True}
    if sal:
        jd.update(salary_min=sal[0], salary_max=sal[1], salary_currency=currency, salary_period=period)
    return jd


# ---------------------------------------------------------------------------
# Location options
# ---------------------------------------------------------------------------

class TestLocations:
    def test_distinct_locations_counted_once_each(self):
        f = jf.build_facets([_jd("a", "Pune"), _jd("b", "Pune"), _jd("c", "Hyderabad")])
        assert [(o["label"], o["count"]) for o in f["location"]] == [("Hyderabad", 1), ("Pune", 2)]

    def test_case_and_spacing_variants_merge(self):
        f = jf.build_facets([_jd("a", "Bangalore"), _jd("b", "bangalore "), _jd("c", "  BANGALORE")])
        assert len(f["location"]) == 1 and f["location"][0]["count"] == 3
        assert f["location"][0]["label"] == "Bangalore"   # first spelling wins

    def test_remote_aliases_merge_and_come_first(self):
        f = jf.build_facets([_jd("a", "Pune"), _jd("b", "remote"), _jd("c", "Work from home"), _jd("d", "WFH")])
        assert f["location"][0] == {"key": "remote", "label": "Remote", "count": 3}

    def test_multi_location_jd_counts_in_each(self):
        f = jf.build_facets([_jd("a", "Hyderabad, Remote"), _jd("b", "Pune / Remote")])
        labels = {o["label"]: o["count"] for o in f["location"]}
        assert labels == {"Remote": 2, "Hyderabad": 1, "Pune": 1}

    def test_new_location_appears_automatically(self):
        before = jf.build_facets([_jd("a", "Pune")])
        after = jf.build_facets([_jd("a", "Pune"), _jd("b", "Chennai"), _jd("c", "pune")])
        assert [o["label"] for o in before["location"]] == ["Pune"]
        assert [o["label"] for o in after["location"]] == ["Chennai", "Pune"]   # Pune not duplicated

    def test_lowercase_new_location_is_title_cased(self):
        assert jf.build_facets([_jd("a", "new york")])["location"][0]["label"] == "New York"

    def test_empty_location_gives_no_option(self):
        assert jf.build_facets([_jd("a", "")])["location"] == []


# ---------------------------------------------------------------------------
# Experience + salary buckets
# ---------------------------------------------------------------------------

class TestBuckets:
    @pytest.mark.parametrize("years, key", [(0, "0-3"), (1, "0-3"), (3, "0-3"), (4, "4-6"),
                                            (6, "4-6"), (7, "7-9"), (10, "10+"), (25, "10+")])
    def test_experience_bucket(self, years, key):
        assert jf.experience_bucket(years) == key

    def test_only_nonempty_experience_buckets_shown_in_order(self):
        f = jf.build_facets([_jd("a", exp=12), _jd("b", exp=2), _jd("c", exp=3)])
        assert [(o["key"], o["count"]) for o in f["experience"]] == [("0-3", 2), ("10+", 1)]

    def test_salary_not_disclosed(self):
        assert jf.salary_buckets(_jd("a")) == ["none"]

    def test_salary_range_spanning_two_buckets(self):
        assert jf.salary_buckets(_jd("a", sal=(800_000, 1_400_000))) == ["inr-5-10", "inr-10-20"]

    def test_salary_bucket_boundary(self):
        # exactly ₹10L belongs to 10–20L, not 5–10L
        assert jf.salary_buckets(_jd("a", sal=(1_000_000, 1_000_000))) == ["inr-10-20"]

    def test_monthly_salary_is_annualised(self):
        # ₹1L / month = ₹12L / year
        assert jf.salary_buckets(_jd("a", sal=(100_000, 100_000), period="month")) == ["inr-10-20"]

    def test_usd_buckets(self):
        assert jf.salary_buckets(_jd("a", sal=(120_000, 160_000), currency="USD")) == ["usd-100-150", "usd-150+"]

    def test_salary_facet_order_and_not_disclosed_last(self):
        f = jf.build_facets([_jd("a"), _jd("b", sal=(2_500_000, 3_000_000)), _jd("c", sal=(300_000, 400_000))])
        assert [o["key"] for o in f["salary"]] == ["inr-0-5", "inr-20-35", "none"]


# ---------------------------------------------------------------------------
# Filtering + sorting
# ---------------------------------------------------------------------------

def _match(jd, score):
    return {"jd": jd, "result": {"overall_score": score}}


MATCHES = [
    _match(_jd("pune_junior", "Pune", 2, (600_000, 900_000), "2026-01-03"), 80),
    _match(_jd("remote_mid", "Remote", 5, (1_500_000, 2_500_000), "2026-01-05"), 60),
    _match(_jd("hyd_remote", "Hyderabad, Remote", 1, None, "2026-01-01"), 70),
    _match(_jd("blr_senior", "Bangalore", 8, (3_000_000, 4_000_000), "2026-01-04"), 50),
]


def _run(**params):
    sel = jf.read_selection(MultiDict([(k, v) for k, vs in params.items()
                                       for v in (vs if isinstance(vs, list) else [vs])]))
    return [m["jd"]["jd_id"] for m in jf.filter_and_sort(MATCHES, sel)]


class TestFilterAndSort:
    def test_default_is_best_match(self):
        assert _run() == ["pune_junior", "hyd_remote", "remote_mid", "blr_senior"]

    def test_location_or_within_filter(self):
        assert _run(loc=["pune", "remote"]) == ["pune_junior", "hyd_remote", "remote_mid"]

    def test_multi_location_jd_matches_either_part(self):
        assert _run(loc="hyderabad") == ["hyd_remote"]

    def test_and_across_filters(self):
        assert _run(loc="remote", exp="4-6") == ["remote_mid"]

    def test_salary_filter_and_not_disclosed(self):
        assert _run(sal="inr-20-35") == ["remote_mid", "blr_senior"]
        assert _run(sal="none") == ["hyd_remote"]

    def test_no_results(self):
        assert _run(loc="pune", exp="10+") == []

    def test_sort_salary_high_undisclosed_last(self):
        assert _run(sort="salary_high") == ["blr_senior", "remote_mid", "pune_junior", "hyd_remote"]

    def test_sort_salary_low(self):
        assert _run(sort="salary_low") == ["pune_junior", "remote_mid", "blr_senior", "hyd_remote"]

    def test_sort_experience(self):
        assert _run(sort="exp_low") == ["hyd_remote", "pune_junior", "remote_mid", "blr_senior"]
        assert _run(sort="exp_high") == ["blr_senior", "remote_mid", "pune_junior", "hyd_remote"]

    def test_sort_newest(self):
        assert _run(sort="newest") == ["remote_mid", "blr_senior", "pune_junior", "hyd_remote"]

    def test_sort_newest_uses_updated_else_posted(self):
        old_but_updated = _jd("old_updated", created="2025-06-01")
        old_but_updated["updated_at"] = "2026-03-01T00:00:00"      # most recent activity
        recent_post = _jd("recent_post", created="2026-02-01")      # never updated → posted time
        stale = _jd("stale", created="2025-01-01")
        items = [_match(stale, 90), _match(recent_post, 50), _match(old_but_updated, 10)]
        sel = jf.read_selection(MultiDict([("sort", "newest")]))
        assert [m["jd"]["jd_id"] for m in jf.filter_and_sort(items, sel)] == ["old_updated", "recent_post", "stale"]

    def test_unknown_sort_falls_back_to_match(self):
        assert jf.read_selection(MultiDict([("sort", "bogus")]))["sort"] == "match"


# ---------------------------------------------------------------------------
# The page
# ---------------------------------------------------------------------------

@pytest.fixture()
def page_client(tmp_path, monkeypatch):
    import auth
    import routes.candidate as cand_routes

    monkeypatch.setattr(auth, "CANDIDATES_FILE", tmp_path / "candidates.json")
    monkeypatch.setattr(auth, "ADMINS_FILE", tmp_path / "admins.json")
    jds_dir = tmp_path / "jds"
    jds_dir.mkdir()
    jds = [m["jd"] for m in MATCHES] + [dict(_jd("closed", "Chennai"), is_active=False)]
    (jds_dir / "jds.json").write_text(json.dumps(jds))
    monkeypatch.setattr(cand_routes, "JDS_DIR", jds_dir)
    parsed_dir = tmp_path / "parsed"
    parsed_dir.mkdir()
    monkeypatch.setattr(cand_routes, "PARSED_RESUMES_DIR", parsed_dir)
    monkeypatch.setattr(cand_routes, "semantic_score", lambda *args, **kwargs: None)

    import main
    app = main.create_app()
    app.config.update(TESTING=True)
    client = app.test_client()
    client.post("/register", data={"name": "C", "email": "c@x.com", "password": "secret1",
                                   "role": "candidate", "target_role": "Dev"})
    cid = auth.load_json(auth.CANDIDATES_FILE)[0]["candidate_id"]
    (parsed_dir / f"{cid}.json").write_text(json.dumps(
        {"raw_text": "x", "skills": ["Python"], "experience_years": 3, "education": "B.Tech"}))
    return client


class TestFindJobsPage:
    def test_filter_bar_lists_options_from_active_jobs(self, page_client):
        html = page_client.get("/candidate/match-jobs").get_data(as_text=True)
        assert 'name="loc" value="remote"' in html and 'name="loc" value="hyderabad"' in html
        assert 'value="chennai"' not in html              # only on an inactive JD
        assert 'name="exp" value="7-9"' in html and 'value="10+"' not in html
        assert 'name="sal" value="none"' in html
        assert "Showing <strong>4</strong> of 4 jobs" in html

    def test_filtered_page_shows_count_chip_and_checked_box(self, page_client):
        html = page_client.get("/candidate/match-jobs?loc=remote").get_data(as_text=True)
        assert "Showing <strong>2</strong> of 4 jobs" in html
        assert 'value="remote" checked' in html
        assert 'class="filter-chip"' in html and "Clear all" in html

    def test_chip_remove_link_drops_only_that_value(self, page_client):
        html = page_client.get("/candidate/match-jobs?loc=remote&loc=pune&sort=exp_low").get_data(as_text=True)
        assert "/candidate/match-jobs?loc=pune&amp;sort=exp_low" in html   # × on "Remote"

    def test_no_results_message(self, page_client):
        html = page_client.get("/candidate/match-jobs?loc=pune&exp=10%2B").get_data(as_text=True)
        assert "No jobs match these filters." in html

    def test_chip_label_for_empty_bucket_and_unknown_location(self, page_client):
        html = page_client.get("/candidate/match-jobs?exp=10%2B&loc=chennai").get_data(as_text=True)
        assert "10+ years" in html          # bucket with no jobs still gets its label
        assert ">Chennai <" in html          # location only on an inactive JD
