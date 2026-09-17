"""High-level LLM service: parse resume, parse JD, explain match.

Each call:
  - resolves + status-gates the model (via factory)
  - acquires a queue slot (bounded concurrency + TTL)
  - runs inside a trace span
  - records a full llm_call DB row (request/response, tokens, latency, status)
  - validates the JSON output; falls back to rule-based on any failure
"""
from __future__ import annotations

import json
import re
import time
from datetime import datetime
from typing import Any, Optional

from db.database import get_session
from db.models_orm import LlmCall
from llm import prompts
from llm.factory import get_llm_client, ModelUnavailableError
from llm.queue_manager import slot, QueueFullError
from observability.logging_config import get_logger
from observability.tracing import span, get_trace_id, get_span_id

_logger = get_logger("llm")


class LlmParseError(Exception):
    """Raised when the LLM cannot produce valid structured output.

    There is intentionally NO rule-based fallback — the LLM is the only parser.
    """


def _safe_json(text: str) -> Optional[dict]:
    """Extract the first JSON object from model output, or None if invalid."""
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if not match:
        return None
    try:
        return json.loads(match.group(0))
    except json.JSONDecodeError:
        return None


def _record_call(task: str, model_id: str, family: str, request_body: str,
                 response_body: str, prompt_tokens: int, completion_tokens: int,
                 latency_ms: float, status: str, error_msg: str = "") -> None:
    """Persist one llm_call row (best-effort; never raises)."""
    try:
        with get_session() as s:
            s.add(LlmCall(
                trace_id=get_trace_id(), span_id=get_span_id(),
                model_id=model_id, family=family, provider="ollama", task=task,
                request_body=request_body[:8000], response_body=response_body[:8000],
                prompt_tokens=prompt_tokens, completion_tokens=completion_tokens,
                latency_ms=latency_ms, status=status, error_msg=error_msg,
                created_at=datetime.utcnow(),
            ))
    except Exception as exc:
        _logger.warning("llm.call_persist_failed", extra={"error": str(exc)})


def _run(task: str, prompt: str, model_id: Optional[str]) -> Optional[dict]:
    """Run one gated, queued, logged LLM call and return parsed JSON or None."""
    try:
        client = get_llm_client(model_id)
    except ModelUnavailableError as exc:
        _logger.warning("llm.model_unavailable", extra={"task": task, "error": str(exc)})
        _record_call(task, model_id or "?", "", prompt, "", 0, 0, 0.0, "rejected", str(exc))
        return None

    with span(f"llm.{task}"):
        try:
            with slot(task):
                start = time.perf_counter()
                result = client.generate(prompt)
                latency_ms = round((time.perf_counter() - start) * 1000, 2)
        except QueueFullError as exc:
            _logger.error("llm.queue_full", extra={"task": task, "error": str(exc)})
            _record_call(task, model_id or "?", "", prompt, "", 0, 0, 0.0, "timeout", str(exc))
            return None
        except Exception as exc:
            _logger.error("llm.call_error", extra={"task": task, "error": str(exc)})
            _record_call(task, model_id or "?", "", prompt, "", 0, 0, 0.0, "error", str(exc))
            return None

    parsed = _safe_json(result.text)
    status = "ok" if parsed is not None else "error"
    _record_call(
        task, result.model_id, "", prompt, result.text,
        result.prompt_tokens, result.completion_tokens, latency_ms, status,
        "" if parsed else "invalid_json",
    )
    _logger.info(f"llm.{task}.done", extra={
        "model_id": result.model_id, "latency_ms": latency_ms,
        "prompt_tokens": result.prompt_tokens, "completion_tokens": result.completion_tokens,
        "parsed_ok": parsed is not None,
    })
    return parsed


def parse_resume_smart(resume_text: str, model_id: Optional[str] = None) -> dict:
    """Extract skills/experience/education/summary via LLM, with rule-based fallback.

    Args:
        resume_text: Raw resume text.
        model_id: Optional model to use; None = active default.

    Returns:
        A dict with keys: raw_text, skills, experience_years, education, summary.
    """
    parsed = _run("parse_resume", prompts.build_resume_parse_prompt(resume_text), model_id)
    if parsed is None:
        # NO rule-based fallback. LLM is the only parser. Fail loudly.
        raise LlmParseError(
            "Resume parsing failed: the LLM did not return valid JSON "
            "(check that Ollama is running and the selected model is active)."
        )
    return {
        "raw_text": resume_text,
        "skills": parsed.get("skills", []),
        "experience_years": float(parsed.get("experience_years", 0) or 0),
        "education": parsed.get("education", "Not specified"),
        "summary": parsed.get("summary", ""),
    }


def parse_jd_smart(jd_text: str, model_id: Optional[str] = None) -> Optional[dict]:
    """Extract required/preferred skills + experience from a JD via LLM.

    Args:
        jd_text: Raw JD text.
        model_id: Optional model to use; None = active default.

    Returns:
        A dict with required_skills/preferred_skills/experience_years/role_title,
        or None if the LLM was unavailable (caller keeps manual input).
    """
    parsed = _run("parse_jd", prompts.build_jd_parse_prompt(jd_text), model_id)
    if not parsed:
        return None
    return {
        "required_skills": parsed.get("required_skills", []),
        "preferred_skills": parsed.get("preferred_skills", []),
        "experience_years": int(parsed.get("experience_years", 0) or 0),
        "role_title": parsed.get("role_title", ""),
    }


def explain_match(resume_text: str, jd_text: str,
                  model_id: Optional[str] = None) -> Optional[dict]:
    """Produce an LLM match score + reasoning for one resume vs one JD.

    Args:
        resume_text: Candidate resume text.
        jd_text: Job description text.
        model_id: Optional model to use; None = active default.

    Returns:
        A dict with overall_score/matched_skills/missing_skills/verdict/reasoning,
        or None if the LLM was unavailable (caller keeps vector/keyword score).
    """
    parsed = _run("explain_match", prompts.build_match_explain_prompt(resume_text, jd_text), model_id)
    if not parsed:
        return None
    return {
        "overall_score": float(parsed.get("overall_score", 0) or 0),
        "matched_skills": parsed.get("matched_skills", []),
        "missing_skills": parsed.get("missing_skills", []),
        "verdict": parsed.get("verdict", ""),
        "reasoning": parsed.get("reasoning", ""),
    }
