"""Unit tests for vector_matcher — semantic scoring logic (model + Chroma faked).

We do NOT load the real sentence-transformer or a real ChromaDB here; both are
replaced with lightweight fakes so the test verifies vector_matcher's own logic
(empty-input guards, id lookup, distance→score mapping) fast and offline.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest

import vector_matcher


class _FakeCollection:
    """Minimal stand-in for a ChromaDB collection."""

    def __init__(self, ids_to_distance, embeddings=None):
        # ids_to_distance: {candidate_id: distance}
        self._data = ids_to_distance
        # embeddings: {id: [embedding_vector]}
        self._embeddings = embeddings or {}

    def count(self):
        return len(self._data)

    def query(self, query_embeddings, n_results, include):
        ids = list(self._data.keys())
        distances = [self._data[i] for i in ids]
        return {"ids": [ids], "distances": [distances]}

    def get(self, ids, include):
        """Retrieve stored embeddings for JD collection."""
        embeddings = []
        for id in ids:
            if id in self._embeddings:
                embeddings.append(self._embeddings[id])
        return {"ids": ids if embeddings else [], "embeddings": embeddings}


@pytest.fixture()
def fake_vectors(monkeypatch):
    """Patch the model + collections so no heavy deps load."""
    class _Vec(list):
        """A list that mimics numpy's .tolist() so embed_text works."""
        def tolist(self):
            return list(self)

    class _FakeModel:
        def encode(self, text):
            return _Vec([0.1, 0.2, 0.3])

    monkeypatch.setattr(vector_matcher, "_get_model", lambda: _FakeModel())

    holder = {
        "resume_collection": _FakeCollection({}),
        "jd_collection": _FakeCollection({})
    }
    monkeypatch.setattr(vector_matcher, "_get_resume_collection", lambda: holder["resume_collection"])
    monkeypatch.setattr(vector_matcher, "_get_jd_collection", lambda: holder["jd_collection"])
    return holder


class TestEmbedText:
    def test_embed_returns_list(self, fake_vectors):
        vec = vector_matcher.embed_text("hello")
        assert vec == [0.1, 0.2, 0.3]


class TestSemanticScore:
    def test_empty_jd_returns_none(self, fake_vectors):
        assert vector_matcher.semantic_score("   ", "c1") is None

    def test_empty_collection_returns_none(self, fake_vectors):
        fake_vectors["resume_collection"] = _FakeCollection({})
        assert vector_matcher.semantic_score("jd text", "c1") is None

    def test_candidate_not_indexed_returns_none(self, fake_vectors):
        fake_vectors["resume_collection"] = _FakeCollection({"other": 0.5})
        assert vector_matcher.semantic_score("jd text", "c1") is None

    def test_distance_maps_to_score(self, fake_vectors):
        # distance 0 → similarity 1/(1+0)=1.0 → 100.0
        fake_vectors["resume_collection"] = _FakeCollection({"c1": 0.0})
        assert vector_matcher.semantic_score("jd text", "c1") == 100.0

    def test_larger_distance_lower_score(self, fake_vectors):
        # distance 1 → similarity 1/2 = 0.5 → 50.0
        fake_vectors["resume_collection"] = _FakeCollection({"c1": 1.0})
        assert vector_matcher.semantic_score("jd text", "c1") == 50.0


class TestSemanticScoreWithJdId:
    """Covers the pre-indexed JD embedding retrieval path (jd_id parameter)."""

    def test_uses_stored_jd_embedding_when_present(self, fake_vectors, monkeypatch):
        # JD collection has a stored embedding for jd1.
        fake_vectors["jd_collection"] = _FakeCollection(
            {}, embeddings={"jd1": [0.9, 0.9, 0.9]}
        )
        fake_vectors["resume_collection"] = _FakeCollection({"c1": 0.0})

        captured = {}
        real_embed_text = vector_matcher.embed_text

        def _tracking_embed_text(text):
            captured["called"] = True
            return real_embed_text(text)

        monkeypatch.setattr(vector_matcher, "embed_text", _tracking_embed_text)

        score = vector_matcher.semantic_score("jd text", "c1", jd_id="jd1")

        assert score == 100.0
        # on-demand embedding must NOT be used for the JD side when a stored
        # embedding is found.
        assert "called" not in captured

    def test_falls_back_when_jd_id_missing_from_store(self, fake_vectors):
        # JD collection has no entry for jd_missing -> falls back to on-demand.
        fake_vectors["jd_collection"] = _FakeCollection({}, embeddings={})
        fake_vectors["resume_collection"] = _FakeCollection({"c1": 0.0})

        score = vector_matcher.semantic_score("jd text", "c1", jd_id="jd_missing")

        assert score == 100.0  # still scores via on-demand embedding fallback

    def test_falls_back_on_collection_error(self, fake_vectors, monkeypatch):
        fake_vectors["resume_collection"] = _FakeCollection({"c1": 0.0})

        class _BrokenCollection(_FakeCollection):
            def get(self, ids, include):
                raise RuntimeError("chroma unavailable")

        monkeypatch.setattr(vector_matcher, "_get_jd_collection", lambda: _BrokenCollection({}))

        score = vector_matcher.semantic_score("jd text", "c1", jd_id="jd1")

        assert score == 100.0  # gracefully falls back, no exception propagates

    def test_no_jd_id_embeds_on_demand(self, fake_vectors):
        fake_vectors["resume_collection"] = _FakeCollection({"c1": 0.0})
        score = vector_matcher.semantic_score("jd text", "c1", jd_id=None)
        assert score == 100.0


class TestIndexResume:
    def test_blank_resume_is_noop(self, fake_vectors):
        calls = {"upsert": 0}

        class _C(_FakeCollection):
            def upsert(self, **kwargs):
                calls["upsert"] += 1

        fake_vectors["resume_collection"] = _C({})
        vector_matcher.index_resume("c1", "   ")
        assert calls["upsert"] == 0

    def test_indexes_nonblank_resume(self, fake_vectors):
        captured = {}

        class _C(_FakeCollection):
            def upsert(self, ids, embeddings, documents, metadatas):
                captured["ids"] = ids
                captured["documents"] = documents

        fake_vectors["resume_collection"] = _C({})
        vector_matcher.index_resume("c1", "real resume text")
        assert captured["ids"] == ["c1"]
        assert captured["documents"] == ["real resume text"]


class TestIndexJD:
    def test_blank_jd_is_noop(self, fake_vectors):
        calls = {"upsert": 0}

        class _C(_FakeCollection):
            def upsert(self, **kwargs):
                calls["upsert"] += 1

        fake_vectors["jd_collection"] = _C({})
        vector_matcher.index_jd("jd1", "   ")
        assert calls["upsert"] == 0

    def test_indexes_nonblank_jd(self, fake_vectors):
        captured = {}

        class _C(_FakeCollection):
            def upsert(self, ids, embeddings, documents, metadatas):
                captured["ids"] = ids
                captured["documents"] = documents

        fake_vectors["jd_collection"] = _C({})
        vector_matcher.index_jd("jd1", "real jd text")
        assert captured["ids"] == ["jd1"]
        assert captured["documents"] == ["real jd text"]


class TestDeleteJD:
    def test_deletes_jd_from_collection(self, fake_vectors):
        captured = {}

        class _C(_FakeCollection):
            def delete(self, ids):
                captured["ids"] = ids

        fake_vectors["jd_collection"] = _C({})
        vector_matcher.delete_jd("jd1")
        assert captured["ids"] == ["jd1"]
