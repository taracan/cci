"""Additional JD embedding tests — edge cases and robustness.

These complement test_jd_embeddings.py with scenarios not covered there:
  - Multiple JDs stored in isolation from each other
  - Score consistency between on-demand and stored-vector paths
  - Double deactivation, empty collections, collection isolation
  - Backfill idempotency (running twice must not duplicate)
  - pipeline.two_stage_match always forwards jd_id
  - semantic_score with jd_id=None explicitly triggers on-demand embedding
  - Route validation rejects bad input before touching Chroma
"""
import hashlib
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest
import vector_matcher


def _fake_embedding(text: str) -> list[float]:
    digest = hashlib.sha256(text.encode()).digest()
    return [b / 255.0 for b in digest[:16]]


@pytest.fixture()
def chroma(monkeypatch):
    embed_calls: list[str] = []
    real_embed = vector_matcher.embed_text

    def _tracking_embed(text):
        embed_calls.append(text)
        return real_embed(text)

    monkeypatch.setattr(vector_matcher, "embed_text", _tracking_embed)
    return {"client": vector_matcher._get_client(), "embed_calls": embed_calls}


def _stored_jd(jd_id: str):
    col = vector_matcher._get_jd_collection()
    return col.get(ids=[jd_id], include=["embeddings", "documents", "metadatas"])


# ─────────────────────────────────────────────
# Multiple JDs — independent isolation
# ─────────────────────────────────────────────

class TestMultipleJdIsolation:
    def test_two_jds_stored_independently(self, chroma):
        vector_matcher.index_jd("jd_1", "Python backend API engineer")
        vector_matcher.index_jd("jd_2", "React frontend UI specialist")

        col = vector_matcher._get_jd_collection()
        assert col.count() == 2
        assert _stored_jd("jd_1")["documents"] == ["Python backend API engineer"]
        assert _stored_jd("jd_2")["documents"] == ["React frontend UI specialist"]

    def test_updating_one_jd_does_not_affect_other(self, chroma):
        vector_matcher.index_jd("jd_1", "Original A")
        vector_matcher.index_jd("jd_2", "Original B")

        vector_matcher.index_jd("jd_1", "Updated A")

        assert _stored_jd("jd_1")["documents"] == ["Updated A"]
        assert _stored_jd("jd_2")["documents"] == ["Original B"]

    def test_deleting_one_jd_leaves_other_intact(self, chroma):
        vector_matcher.index_jd("jd_1", "Keep this")
        vector_matcher.index_jd("jd_2", "Delete this")

        vector_matcher.delete_jd("jd_2")

        assert vector_matcher._get_jd_collection().count() == 1
        assert _stored_jd("jd_1")["ids"] == ["jd_1"]
        assert _stored_jd("jd_2")["ids"] == []

    def test_each_jd_has_correct_metadata(self, chroma):
        vector_matcher.index_jd("jd_meta", "Text")
        row = _stored_jd("jd_meta")
        assert row["metadatas"] == [{"jd_id": "jd_meta"}]


# ─────────────────────────────────────────────
# Score consistency: stored path == on-demand path
# ─────────────────────────────────────────────

class TestScoreConsistency:
    def test_stored_and_on_demand_give_same_score(self, chroma):
        """Both paths must produce identical scores for the same JD/resume pair."""
        vector_matcher.index_resume("cand_1", "Python Flask SQL developer")
        vector_matcher.index_jd("jd_a", "Python Flask SQL backend job")

        stored_score = vector_matcher.semantic_score(
            "Python Flask SQL backend job", "cand_1", jd_id="jd_a"
        )
        on_demand_score = vector_matcher.semantic_score(
            "Python Flask SQL backend job", "cand_1"
        )

        assert stored_score == on_demand_score

    def test_different_jds_give_different_scores(self, chroma):
        """Two distinct JDs must produce different scores against the same resume.

        Note: the test suite uses a hash-based fake model that has no semantic
        understanding, so we can only assert the scores differ — not which is
        higher. The real MiniLM model would rank the related JD higher.
        """
        vector_matcher.index_resume("cand_1", "Python Flask SQL developer")
        vector_matcher.index_jd("jd_a", "Python Flask SQL backend job")
        vector_matcher.index_jd("jd_b", "Marine biology deep sea research")

        score_a = vector_matcher.semantic_score(
            "Python Flask SQL backend job", "cand_1", jd_id="jd_a"
        )
        score_b = vector_matcher.semantic_score(
            "Marine biology deep sea research", "cand_1", jd_id="jd_b"
        )

        assert score_a is not None
        assert score_b is not None
        assert score_a != score_b, "Two distinct JDs must produce different scores"


# ─────────────────────────────────────────────
# Collection isolation — resumes and jds never mix
# ─────────────────────────────────────────────

class TestCollectionIsolation:
    def test_jd_index_does_not_pollute_resume_collection(self, chroma):
        vector_matcher.index_jd("jd_a", "Some job description text")

        resume_col = vector_matcher._get_resume_collection()
        assert resume_col.count() == 0

    def test_resume_index_does_not_pollute_jd_collection(self, chroma):
        vector_matcher.index_resume("cand_1", "Some resume text here")

        jd_col = vector_matcher._get_jd_collection()
        assert jd_col.count() == 0

    def test_both_collections_exist_independently(self, chroma):
        vector_matcher.index_jd("jd_a", "JD text")
        vector_matcher.index_resume("cand_1", "Resume text")

        names = {c.name for c in chroma["client"].list_collections()}
        assert vector_matcher.JD_COLLECTION in names
        assert vector_matcher.RESUME_COLLECTION in names

        assert vector_matcher._get_jd_collection().count() == 1
        assert vector_matcher._get_resume_collection().count() == 1


# ─────────────────────────────────────────────
# Edge cases: empty collection, explicit None jd_id
# ─────────────────────────────────────────────

class TestEdgeCases:
    def test_semantic_score_returns_none_when_no_resumes(self, chroma):
        """No resumes indexed → semantic_score must return None without crashing."""
        score = vector_matcher.semantic_score("some JD text", "cand_missing")
        assert score is None

    def test_semantic_score_none_jd_id_always_embeds(self, chroma):
        """Passing jd_id=None explicitly must always take the on-demand path."""
        vector_matcher.index_resume("cand_1", "resume text")
        vector_matcher.index_jd("jd_a", "JD text")
        chroma["embed_calls"].clear()

        vector_matcher.semantic_score("JD text", "cand_1", jd_id=None)

        assert chroma["embed_calls"] == ["JD text"], "jd_id=None must trigger on-demand embed"

    def test_semantic_score_candidate_not_indexed_returns_none(self, chroma):
        """Candidate with no stored resume vector → score is None."""
        vector_matcher.index_jd("jd_a", "JD text")
        score = vector_matcher.semantic_score("JD text", "ghost_candidate", jd_id="jd_a")
        assert score is None

    def test_double_deactivation_is_safe(self, chroma):
        """Deactivating an already-inactive JD must not raise and collection stays empty."""
        vector_matcher.index_jd("jd_a", "text")
        vector_matcher.delete_jd("jd_a")
        vector_matcher.delete_jd("jd_a")  # second delete — must not raise
        assert vector_matcher._get_jd_collection().count() == 0

    def test_jd_text_whitespace_only_not_stored(self, chroma):
        """Whitespace-only JD text variants must all be rejected without storing."""
        for blank in ["", "   ", "\t", "\n\n"]:
            vector_matcher.index_jd(f"jd_{hash(blank)}", blank)
        assert vector_matcher._get_jd_collection().count() == 0


# ─────────────────────────────────────────────
# Backfill idempotency
# ─────────────────────────────────────────────

class TestBackfillIdempotency:
    def test_running_backfill_twice_does_not_duplicate(self, chroma, tmp_path, monkeypatch):
        import backfill_vectors

        jds_dir = tmp_path / "bf"
        jds_dir.mkdir()
        (jds_dir / "jds.json").write_text(json.dumps([
            {"jd_id": "jd_idem", "jd_text": "idempotent test JD", "is_active": True},
        ]))
        monkeypatch.setattr(backfill_vectors, "JDS_DIR", jds_dir)

        backfill_vectors.backfill_jds()
        backfill_vectors.backfill_jds()  # second run

        col = vector_matcher._get_jd_collection()
        assert col.count() == 1, "Backfill ran twice but count is not 1 — duplicates exist"

    def test_backfill_updates_text_on_second_run(self, chroma, tmp_path, monkeypatch):
        import backfill_vectors

        jds_dir = tmp_path / "bf2"
        jds_dir.mkdir()

        (jds_dir / "jds.json").write_text(json.dumps([
            {"jd_id": "jd_up", "jd_text": "first text", "is_active": True},
        ]))
        monkeypatch.setattr(backfill_vectors, "JDS_DIR", jds_dir)
        backfill_vectors.backfill_jds()

        (jds_dir / "jds.json").write_text(json.dumps([
            {"jd_id": "jd_up", "jd_text": "updated text", "is_active": True},
        ]))
        backfill_vectors.backfill_jds()

        col = vector_matcher._get_jd_collection()
        assert col.count() == 1
        row = col.get(ids=["jd_up"], include=["documents"])
        assert row["documents"] == ["updated text"]


# ─────────────────────────────────────────────
# pipeline.two_stage_match always forwards jd_id
# ─────────────────────────────────────────────

class TestPipelineAlwaysForwardsJdId:
    def test_jd_id_forwarded_for_every_candidate(self, monkeypatch):
        """two_stage_match must pass jd_id to semantic_score for ALL candidates."""
        import pipeline.matcher as matcher

        seen: list[str | None] = []
        monkeypatch.setattr(
            matcher, "semantic_score",
            lambda text, cid, jd_id=None: seen.append(jd_id) or 50.0,
        )
        monkeypatch.setattr(matcher, "explain_match", lambda *a, **k: None)

        jd = {
            "jd_id": "jd_pipeline",
            "jd_text": "text",
            "required_skills": ["Python"],
            "experience_years": 2,
            "role_title": "Dev",
        }
        candidates = [{"candidate_id": "c1"}, {"candidate_id": "c2"}, {"candidate_id": "c3"}]
        matcher.two_stage_match(jd, candidates, lambda cid: {"skills": ["Python"], "experience_years": 3})

        assert all(jd_id == "jd_pipeline" for jd_id in seen), (
            f"Not all calls forwarded jd_id='jd_pipeline'. Got: {seen}"
        )
        assert len(seen) == 3, f"Expected 3 semantic_score calls, got {len(seen)}"

    def test_jd_without_jd_id_key_passes_none(self, monkeypatch):
        """If jd dict has no jd_id key, two_stage_match must pass None (not crash)."""
        import pipeline.matcher as matcher

        seen: list[str | None] = []
        monkeypatch.setattr(
            matcher, "semantic_score",
            lambda text, cid, jd_id=None: seen.append(jd_id) or 50.0,
        )
        monkeypatch.setattr(matcher, "explain_match", lambda *a, **k: None)

        jd = {
            "jd_text": "text",
            "required_skills": ["Python"],
            "experience_years": 2,
            "role_title": "Dev",
            # no jd_id key
        }
        matcher.two_stage_match(jd, [{"candidate_id": "c1"}],
                                lambda cid: {"skills": ["Python"], "experience_years": 3})
        assert seen == [None]


# ─────────────────────────────────────────────
# Route validation: bad input never touches Chroma
# ─────────────────────────────────────────────

@pytest.fixture()
def app_env(chroma, tmp_path, monkeypatch):
    import auth
    import routes.admin as admin_routes
    import routes.candidate as candidate_routes
    from config import ADMIN_KEY
    import pipeline.matcher as matcher

    monkeypatch.setattr(auth, "CANDIDATES_FILE", tmp_path / "candidates.json")
    monkeypatch.setattr(auth, "ADMINS_FILE", tmp_path / "admins.json")

    jds_dir = tmp_path / "jds"
    parsed_dir = tmp_path / "parsed"
    jds_dir.mkdir()
    parsed_dir.mkdir()
    monkeypatch.setattr(admin_routes, "JDS_FILE", jds_dir / "jds.json")
    monkeypatch.setattr(admin_routes, "PARSED_RESUMES_DIR", parsed_dir)
    monkeypatch.setattr(candidate_routes, "JDS_DIR", jds_dir)
    monkeypatch.setattr(candidate_routes, "PARSED_RESUMES_DIR", parsed_dir)
    monkeypatch.setattr(matcher, "explain_match", lambda *a, **k: None)

    import main
    app = main.create_app()
    app.config.update(TESTING=True)
    return {
        "app": app,
        "admin_key": ADMIN_KEY,
        "jds_file": jds_dir / "jds.json",
        "parsed_dir": parsed_dir,
        **chroma,
    }


def _admin_client(env):
    client = env["app"].test_client()
    client.post("/register", data={
        "name": "Admin", "email": "admin@x.com", "password": "secret1",
        "role": "admin", "admin_key": env["admin_key"],
    })
    return client


class TestRouteValidation:
    def test_upload_missing_role_title_rejected_chroma_untouched(self, app_env):
        client = _admin_client(app_env)
        resp = client.post("/admin/upload-jd", data={
            # role_title missing
            "company": "Acme", "required_skills": "Python",
            "jd_text": "Some valid JD text here.",
        })
        assert resp.status_code == 200  # re-renders the form, not a redirect
        assert vector_matcher._get_jd_collection().count() == 0

    def test_upload_missing_company_rejected_chroma_untouched(self, app_env):
        client = _admin_client(app_env)
        resp = client.post("/admin/upload-jd", data={
            "role_title": "Engineer",
            # company missing
            "required_skills": "Python",
            "jd_text": "Some valid JD text.",
        })
        assert resp.status_code == 200
        assert vector_matcher._get_jd_collection().count() == 0

    def test_upload_missing_jd_text_rejected_chroma_untouched(self, app_env):
        client = _admin_client(app_env)
        resp = client.post("/admin/upload-jd", data={
            "role_title": "Engineer", "company": "Acme",
            "required_skills": "Python",
            # jd_text missing
        })
        assert resp.status_code == 200
        assert vector_matcher._get_jd_collection().count() == 0

    def test_successful_upload_then_deactivate_leaves_zero_embeddings(self, app_env):
        """Full lifecycle: upload → deactivate → Chroma ends up empty."""
        client = _admin_client(app_env)
        resp = client.post("/admin/upload-jd", data={
            "role_title": "Backend Engineer", "company": "Acme",
            "required_skills": "Python", "preferred_skills": "",
            "experience_years": "3", "jd_text": "Build Python APIs", "location": "Remote",
        })
        assert resp.status_code == 302
        jd_id = json.loads(app_env["jds_file"].read_text())[0]["jd_id"]
        assert vector_matcher._get_jd_collection().count() == 1

        client.post(f"/admin/jds/{jd_id}/delete")
        assert vector_matcher._get_jd_collection().count() == 0
