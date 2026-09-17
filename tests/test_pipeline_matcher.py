"""Unit tests for pipeline.matcher.two_stage_match.

The funnel must:
  1. Stage 1 (cheap): score EVERY candidate via embeddings + keyword.
  2. Sort by score descending.
  3. Stage 2 (expensive): run the LLM ONLY on the top-K; others get llm=None.

Both the embedding score and the LLM are faked so the test is fast + offline.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest

from pipeline import matcher


JD = {
    "jd_text": "Backend Python role",
    "required_skills": ["Python"],
    "preferred_skills": ["AWS"],
    "experience_years": 3,
    "role_title": "Backend Dev",
}


def _make_candidates(n: int) -> list[dict]:
    return [{"candidate_id": f"c{i}", "target_role": "Backend Dev"} for i in range(n)]


def _loader(parsed_map):
    return lambda cid: parsed_map.get(cid)


@pytest.fixture()
def patched(monkeypatch):
    """Fake semantic_score (by candidate id) and explain_match (records calls)."""
    # semantic score = higher for higher-index candidates so ordering is predictable.
    sem_scores = {}

    def fake_sem(jd_text, cid):
        return sem_scores.get(cid, 0.0)

    llm_calls = []

    def fake_explain(resume_text, jd_text, model_id=None):
        llm_calls.append(resume_text)
        return {"overall_score": 90.0, "verdict": "Strong Match",
                "matched_skills": ["Python"], "missing_skills": [], "reasoning": "ok"}

    monkeypatch.setattr(matcher, "semantic_score", fake_sem)
    monkeypatch.setattr(matcher, "explain_match", fake_explain)
    return sem_scores, llm_calls


class TestTwoStageMatch:
    def test_all_candidates_scored_stage1(self, patched):
        sem_scores, _ = patched
        cands = _make_candidates(3)
        parsed = {c["candidate_id"]: {"skills": ["Python"], "experience_years": 3,
                                      "raw_text": f"resume {c['candidate_id']}"}
                  for c in cands}
        results = matcher.two_stage_match(JD, cands, _loader(parsed), top_k=2)
        assert len(results) == 3  # every candidate with a parsed resume is scored

    def test_candidates_without_parsed_resume_skipped(self, patched):
        cands = _make_candidates(3)
        # only c0 has a parsed resume
        parsed = {"c0": {"skills": ["Python"], "experience_years": 3, "raw_text": "r0"}}
        results = matcher.two_stage_match(JD, cands, _loader(parsed), top_k=5)
        assert len(results) == 1
        assert results[0]["candidate"]["candidate_id"] == "c0"

    def test_llm_only_runs_on_top_k(self, patched):
        sem_scores, llm_calls = patched
        cands = _make_candidates(4)
        # Make c3 > c2 > c1 > c0 by semantic score.
        for i, c in enumerate(cands):
            sem_scores[c["candidate_id"]] = float(i * 10)
        parsed = {c["candidate_id"]: {"skills": ["Python"], "experience_years": 3,
                                      "raw_text": f"resume-{c['candidate_id']}"}
                  for c in cands}

        results = matcher.two_stage_match(JD, cands, _loader(parsed), top_k=2)

        # LLM ran exactly twice (top 2 only).
        assert len(llm_calls) == 2
        with_llm = [r for r in results if r.get("llm")]
        without_llm = [r for r in results if r.get("llm") is None]
        assert len(with_llm) == 2
        assert len(without_llm) == 2

    def test_results_sorted_descending(self, patched):
        sem_scores, _ = patched
        cands = _make_candidates(3)
        sem_scores["c0"], sem_scores["c1"], sem_scores["c2"] = 5.0, 95.0, 50.0
        parsed = {c["candidate_id"]: {"skills": ["Python"], "experience_years": 3,
                                      "raw_text": "r"} for c in cands}
        results = matcher.two_stage_match(JD, cands, _loader(parsed), top_k=1)
        scores = [r["result"]["overall_score"] for r in results]
        assert scores == sorted(scores, reverse=True)
