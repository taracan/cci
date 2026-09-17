"""Two-stage matching funnel.

Stage 1 (cheap, all candidates): embeddings (semantic) + keyword scores → rank
          → keep top-K.
Stage 2 (expensive, top-K only): LLM explain_match adds score + reasoning.

Every stage is wrapped in a trace span so one request produces one trace_id with
multiple span_ids (embed span, then an llm span per shortlisted candidate).
"""
from __future__ import annotations

from typing import Any, Optional

from config import MATCH_TOP_K
from matching_engine import calculate_match
from vector_matcher import semantic_score
from llm.llm_service import explain_match
from observability.logging_config import get_logger
from observability.tracing import span

_logger = get_logger("matcher")


def two_stage_match(jd: dict, candidates: list[dict],
                    load_parsed, model_id: Optional[str] = None,
                    top_k: int = MATCH_TOP_K) -> list[dict]:
    """Rank candidates against a JD using embeddings, then LLM on the top-K.

    Args:
        jd: The job description dict (must include jd_text, required_skills, etc.).
        candidates: List of candidate dicts.
        load_parsed: Callable(candidate_id) -> parsed resume dict or None.
        model_id: LLM model to use for stage 2 (None = active default).
        top_k: How many top candidates get the LLM stage.

    Returns:
        A list of result dicts sorted by final score descending. Each contains
        the candidate, parsed resume, stage-1 result, and optional llm result.
    """
    jd_text = jd.get("jd_text", "")

    # ---- STAGE 1: embeddings + keyword, for EVERY candidate (cheap) ----
    stage1: list[dict] = []
    with span("match.stage1_shortlist"):
        for c in candidates:
            cid = c["candidate_id"]
            parsed = load_parsed(cid)
            if not parsed:
                continue
            sem = semantic_score(jd_text, cid)
            result = calculate_match(
                candidate_skills=parsed.get("skills", []),
                candidate_exp=parsed.get("experience_years", 0),
                candidate_role=c.get("target_role", ""),
                jd_required=jd.get("required_skills", []),
                jd_preferred=jd.get("preferred_skills", []),
                jd_exp=jd.get("experience_years", 0),
                jd_title=jd.get("role_title", ""),
                semantic_score=sem,
            )
            stage1.append({"candidate": c, "parsed": parsed, "result": result})

        stage1.sort(key=lambda x: x["result"]["overall_score"], reverse=True)
        _logger.info("match.stage1.done", extra={
            "total_candidates": len(candidates), "scored": len(stage1), "top_k": top_k,
        })

    shortlist = stage1[:top_k]

    # ---- STAGE 2: LLM explanation, ONLY for the top-K (expensive) ----
    with span("match.stage2_llm"):
        for item in shortlist:
            cid = item["candidate"]["candidate_id"]
            resume_text = item["parsed"].get("raw_text", "")
            llm = explain_match(resume_text, jd_text, model_id=model_id)
            item["llm"] = llm
            _logger.info("match.stage2.candidate", extra={
                "candidate_id": cid,
                "llm_used": llm is not None,
                "llm_score": (llm or {}).get("overall_score"),
            })

    # Candidates beyond top-K keep stage-1 only (no llm key).
    for item in stage1[top_k:]:
        item["llm"] = None

    return stage1
