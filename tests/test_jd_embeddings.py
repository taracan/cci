"""JD embedding tests: JDs are embedded, stored in ChromaDB, and used for matching.

Unlike test_vector_matcher.py (which fakes the collection), these tests run a
REAL ChromaDB PersistentClient in a tmp dir. That is still fully offline, and it
catches behaviour the fakes can't — e.g. Chroma returning embeddings as a numpy
array rather than a list. The sentence-transformer model is replaced by the
deterministic hash-based fake from conftest's autouse _isolated_vector_store
fixture, which also points Chroma at a per-test tmp dir.

Covers the PR #10 review points:
  1. Deactivating a JD removes its embedding (and edits / backfill keep it out).
  2. Matching callers pass jd_id, so matching uses the pre-indexed JD.
  3. semantic_score reads the stored JD embedding instead of re-embedding.
"""
import hashlib
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import chromadb
import pytest

import vector_matcher

def _fake_embedding(text: str) -> list[float]:
    """Mirror of conftest's fake model: same text → same 16-dim vector."""
    digest = hashlib.sha256(text.encode()).digest()
    return [b / 255.0 for b in digest[:16]]


@pytest.fixture()
def chroma(monkeypatch):
    """The tmp Chroma client from conftest plus a record of every embed_text call.

    Returns a dict with:
      client       – the per-test PersistentClient
      embed_calls  – list of every text passed to embed_text
    """
    embed_calls: list[str] = []
    real_embed = vector_matcher.embed_text

    def _tracking_embed(text):
        embed_calls.append(text)
        return real_embed(text)

    monkeypatch.setattr(vector_matcher, "embed_text", _tracking_embed)
    return {"client": vector_matcher._get_client(), "embed_calls": embed_calls}


def _stored_jd(jd_id: str):
    """Fetch a JD row straight from the Chroma JD collection."""
    col = vector_matcher._get_jd_collection()
    return col.get(ids=[jd_id], include=["embeddings", "documents", "metadatas"])


# ─────────────────────────────────────────────
# vector_matcher against a real Chroma store
# ─────────────────────────────────────────────

class TestIndexJdStoresInChroma:
    def test_jd_embedding_is_persisted(self, chroma):
        vector_matcher.index_jd("jd_a", "Python backend engineer building APIs")

        row = _stored_jd("jd_a")
        assert row["ids"] == ["jd_a"]
        assert row["documents"] == ["Python backend engineer building APIs"]
        assert row["metadatas"] == [{"jd_id": "jd_a"}]
        stored = [round(float(x), 5) for x in row["embeddings"][0]]
        expected = [round(x, 5) for x in _fake_embedding("Python backend engineer building APIs")]
        assert stored == expected

    def test_uses_dedicated_jd_collection(self, chroma):
        vector_matcher.index_jd("jd_a", "some JD")
        names = {c.name for c in chroma["client"].list_collections()}
        assert vector_matcher.JD_COLLECTION in names
        assert vector_matcher._get_jd_collection().count() == 1

    def test_reindex_overwrites_not_duplicates(self, chroma):
        vector_matcher.index_jd("jd_a", "old text")
        vector_matcher.index_jd("jd_a", "new text")

        col = vector_matcher._get_jd_collection()
        assert col.count() == 1
        assert _stored_jd("jd_a")["documents"] == ["new text"]

    def test_blank_jd_not_stored(self, chroma):
        vector_matcher.index_jd("jd_blank", "   ")
        assert vector_matcher._get_jd_collection().count() == 0

    def test_embedding_persists_across_clients(self, chroma):
        vector_matcher.index_jd("jd_a", "persisted JD")

        # A brand-new client on the same dir (like an app restart) still sees it.
        fresh = chromadb.PersistentClient(path=str(vector_matcher.CHROMA_DIR))
        col = fresh.get_collection(vector_matcher.JD_COLLECTION)
        assert col.get(ids=["jd_a"])["ids"] == ["jd_a"]

    def test_delete_jd_removes_embedding(self, chroma):
        vector_matcher.index_jd("jd_a", "to be removed")
        vector_matcher.delete_jd("jd_a")
        assert _stored_jd("jd_a")["ids"] == []

    def test_delete_missing_jd_is_harmless(self, chroma):
        vector_matcher.delete_jd("never_indexed")  # must not raise


class TestSemanticScoreUsesStoredJd:
    def test_stored_embedding_used_no_reembed(self, chroma):
        """Review point 3: the stored-embedding path must actually be taken."""
        vector_matcher.index_resume("cand_1", "resume text")
        vector_matcher.index_jd("jd_a", "JD text")
        chroma["embed_calls"].clear()

        score = vector_matcher.semantic_score("JD text", "cand_1", jd_id="jd_a")

        assert score is not None
        assert chroma["embed_calls"] == [], "JD was re-embedded instead of read from Chroma"

    def test_score_comes_from_stored_vector_not_passed_text(self, chroma):
        """If the stored vector is used, the passed jd_text doesn't affect the score."""
        vector_matcher.index_resume("cand_1", "resume text")
        vector_matcher.index_jd("jd_a", "JD text")

        via_store = vector_matcher.semantic_score("different text", "cand_1", jd_id="jd_a")
        on_demand = vector_matcher.semantic_score("JD text", "cand_1")
        assert via_store == on_demand

    def test_falls_back_to_on_demand_when_jd_not_indexed(self, chroma):
        vector_matcher.index_resume("cand_1", "resume text")
        chroma["embed_calls"].clear()

        score = vector_matcher.semantic_score("JD text", "cand_1", jd_id="jd_missing")

        assert score is not None
        assert chroma["embed_calls"] == ["JD text"]

    def test_deleted_jd_falls_back_to_on_demand(self, chroma):
        vector_matcher.index_resume("cand_1", "resume text")
        vector_matcher.index_jd("jd_a", "JD text")
        vector_matcher.delete_jd("jd_a")
        chroma["embed_calls"].clear()

        vector_matcher.semantic_score("JD text", "cand_1", jd_id="jd_a")
        assert chroma["embed_calls"] == ["JD text"]

    def test_identical_text_scores_100(self, chroma):
        vector_matcher.index_resume("cand_1", "same words")
        vector_matcher.index_jd("jd_a", "same words")
        assert vector_matcher.semantic_score("same words", "cand_1", jd_id="jd_a") == 100.0


# ─────────────────────────────────────────────
# Routes: JD lifecycle keeps Chroma in sync with jds.json
# ─────────────────────────────────────────────

@pytest.fixture()
def app_env(chroma, tmp_path, monkeypatch):
    """Flask test client with auth, JD and parsed-resume data in tmp_path."""
    import auth
    import routes.admin as admin_routes
    import routes.candidate as candidate_routes
    from config import ADMIN_KEY

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

    # Stage 2 LLM is out of scope here — keep it offline.
    import pipeline.matcher as matcher
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


def _upload_jd(client, env, text="Build Python APIs with Flask and PostgreSQL."):
    resp = client.post("/admin/upload-jd", data={
        "role_title": "Backend Engineer", "company": "Acme",
        "required_skills": "Python, SQL", "preferred_skills": "AWS",
        "experience_years": "3", "jd_text": text, "location": "Remote",
    })
    assert resp.status_code == 302
    return json.loads(env["jds_file"].read_text())[-1]["jd_id"]


class TestJdRoutesSyncChroma:
    def test_upload_embeds_jd(self, app_env):
        client = _admin_client(app_env)
        jd_id = _upload_jd(client, app_env, text="Upload me")

        assert _stored_jd(jd_id)["documents"] == ["Upload me"]

    def test_edit_reembeds_updated_text(self, app_env):
        client = _admin_client(app_env)
        jd_id = _upload_jd(client, app_env, text="Original text")

        client.post(f"/admin/jds/{jd_id}/edit", data={
            "role_title": "Backend Engineer", "company": "Acme",
            "required_skills": "Python", "preferred_skills": "",
            "experience_years": "3", "jd_text": "Edited text", "location": "Remote",
        })

        row = _stored_jd(jd_id)
        assert row["documents"] == ["Edited text"]
        assert vector_matcher._get_jd_collection().count() == 1

    def test_deactivate_removes_embedding(self, app_env):
        """Review point 1: soft delete must also remove the Chroma embedding."""
        client = _admin_client(app_env)
        jd_id = _upload_jd(client, app_env)
        assert _stored_jd(jd_id)["ids"] == [jd_id]

        client.post(f"/admin/jds/{jd_id}/delete")

        assert json.loads(app_env["jds_file"].read_text())[0]["is_active"] is False
        assert _stored_jd(jd_id)["ids"] == []

    def test_reactivate_restores_embedding(self, app_env):
        client = _admin_client(app_env)
        jd_id = _upload_jd(client, app_env, text="Comes back")
        client.post(f"/admin/jds/{jd_id}/delete")

        client.post(f"/admin/jds/{jd_id}/reactivate")

        assert _stored_jd(jd_id)["documents"] == ["Comes back"]

    def test_editing_inactive_jd_does_not_reindex(self, app_env):
        client = _admin_client(app_env)
        jd_id = _upload_jd(client, app_env, text="Before")
        client.post(f"/admin/jds/{jd_id}/delete")

        client.post(f"/admin/jds/{jd_id}/edit", data={
            "role_title": "Backend Engineer", "company": "Acme",
            "required_skills": "Python", "preferred_skills": "",
            "experience_years": "3", "jd_text": "After", "location": "Remote",
        })
        assert _stored_jd(jd_id)["ids"] == []

        # Reactivation picks up the edited text.
        client.post(f"/admin/jds/{jd_id}/reactivate")
        assert _stored_jd(jd_id)["documents"] == ["After"]


# ─────────────────────────────────────────────
# Integration: matching uses the pre-indexed JD
# ─────────────────────────────────────────────

class TestMatchingUsesPreIndexedJd:
    """Review point 2: callers pass jd_id, so no JD is re-embedded at match time."""

    def _setup_candidate(self, env, client):
        client.post("/register", data={
            "name": "Alice", "email": "alice@x.com", "password": "secret1",
            "target_role": "Backend Engineer", "role": "candidate",
        })
        cid = json.loads((env["jds_file"].parent.parent / "candidates.json").read_text())[0]["candidate_id"]
        resume_text = "Python developer with Flask and SQL experience."
        (env["parsed_dir"] / f"{cid}.json").write_text(json.dumps({
            "skills": ["Python", "SQL"], "experience_years": 4,
            "education": "BSc", "summary": "dev", "raw_text": resume_text,
        }))
        vector_matcher.index_resume(cid, resume_text)
        return cid

    def test_candidate_match_jobs_uses_stored_jd(self, app_env):
        admin = _admin_client(app_env)
        jd_id = _upload_jd(admin, app_env)

        cand = app_env["app"].test_client()
        self._setup_candidate(app_env, cand)
        app_env["embed_calls"].clear()

        resp = cand.get("/candidate/match-jobs")

        assert resp.status_code == 200
        assert b"Backend Engineer" in resp.data
        assert app_env["embed_calls"] == [], "match-jobs re-embedded the JD"

    def test_candidate_match_detail_uses_stored_jd(self, app_env):
        admin = _admin_client(app_env)
        jd_id = _upload_jd(admin, app_env)

        cand = app_env["app"].test_client()
        self._setup_candidate(app_env, cand)
        app_env["embed_calls"].clear()

        resp = cand.get(f"/candidate/match-jobs/{jd_id}")

        assert resp.status_code == 200
        assert app_env["embed_calls"] == [], "match detail re-embedded the JD"

    def test_admin_ranking_uses_stored_jd(self, app_env):
        admin = _admin_client(app_env)
        jd_id = _upload_jd(admin, app_env)

        self._setup_candidate(app_env, app_env["app"].test_client())
        app_env["embed_calls"].clear()

        resp = admin.get(f"/admin/candidates-for-jd/{jd_id}")

        assert resp.status_code == 200
        assert b"Alice" in resp.data
        assert app_env["embed_calls"] == [], "two-stage ranking re-embedded the JD"

    def test_pipeline_passes_jd_id(self, monkeypatch):
        import pipeline.matcher as matcher
        seen = []
        monkeypatch.setattr(matcher, "semantic_score",
                            lambda text, cid, jd_id=None: seen.append(jd_id) or 50.0)
        monkeypatch.setattr(matcher, "explain_match", lambda *a, **k: None)

        jd = {"jd_id": "jd_x", "jd_text": "t", "required_skills": ["Python"],
              "experience_years": 1, "role_title": "Dev"}
        matcher.two_stage_match(jd, [{"candidate_id": "c1"}],
                                lambda cid: {"skills": ["Python"], "experience_years": 2})
        assert seen == ["jd_x"]


# ─────────────────────────────────────────────
# backfill_vectors: only active JDs are indexed
# ─────────────────────────────────────────────

class TestBackfillJds:
    def test_backfill_indexes_active_and_skips_inactive(self, chroma, tmp_path, monkeypatch):
        import backfill_vectors
        jds_dir = tmp_path / "bf_jds"
        jds_dir.mkdir()
        (jds_dir / "jds.json").write_text(json.dumps([
            {"jd_id": "jd_on", "jd_text": "active JD", "is_active": True},
            {"jd_id": "jd_off", "jd_text": "inactive JD", "is_active": False},
            {"jd_id": "jd_empty", "jd_text": "   ", "is_active": True},
        ]))
        monkeypatch.setattr(backfill_vectors, "JDS_DIR", jds_dir)

        backfill_vectors.backfill_jds()

        col = vector_matcher._get_jd_collection()
        assert col.get(ids=["jd_on", "jd_off", "jd_empty"])["ids"] == ["jd_on"]
