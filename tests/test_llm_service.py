"""Unit tests for llm.llm_service — parsing with a FAKE LLM (no Ollama needed).

The `fake_llm` fixture (conftest) patches the Ollama client so we control the
raw text the 'model' returns and assert how the service parses/validates it.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest

from llm import llm_service
from llm.llm_service import (
    parse_resume_smart, parse_jd_smart, explain_match, LlmParseError, _safe_json,
)


class TestSafeJson:
    def test_extracts_clean_json(self):
        assert _safe_json('{"a": 1}') == {"a": 1}

    def test_extracts_json_from_surrounding_text(self):
        assert _safe_json('here is: {"a": 1} thanks') == {"a": 1}

    def test_invalid_json_returns_none(self):
        assert _safe_json("no json here") is None

    def test_broken_json_returns_none(self):
        assert _safe_json('{"a": }') is None


class TestParseResumeSmart:
    def test_valid_llm_output(self, fake_llm, db_session):
        fake_llm.response = ('{"skills": ["Python", "FastAPI"], '
                             '"experience_years": 4, "education": "B.Tech", "summary": "x"}')
        r = parse_resume_smart("resume text")
        assert r["skills"] == ["Python", "FastAPI"]
        assert r["experience_years"] == 4.0
        assert r["education"] == "B.Tech"
        assert r["raw_text"] == "resume text"

    def test_invalid_json_raises_no_fallback(self, fake_llm, db_session):
        fake_llm.response = "the model rambled with no json"
        with pytest.raises(LlmParseError):
            parse_resume_smart("resume text")

    def test_missing_fields_get_defaults(self, fake_llm, db_session):
        fake_llm.response = '{"skills": ["Go"]}'
        r = parse_resume_smart("x")
        assert r["skills"] == ["Go"]
        assert r["experience_years"] == 0.0
        assert r["education"] == "Not specified"


class TestParseJdSmart:
    def test_valid_output(self, fake_llm, db_session):
        fake_llm.response = ('{"required_skills": ["Python"], "preferred_skills": ["AWS"], '
                             '"experience_years": 3, "role_title": "Backend Dev"}')
        r = parse_jd_smart("jd text")
        assert r["required_skills"] == ["Python"]
        assert r["experience_years"] == 3
        assert r["role_title"] == "Backend Dev"

    def test_invalid_returns_none(self, fake_llm, db_session):
        fake_llm.response = "no json"
        assert parse_jd_smart("jd text") is None


class TestExplainMatch:
    def test_valid_output(self, fake_llm, db_session):
        fake_llm.response = ('{"overall_score": 82.5, "matched_skills": ["Python"], '
                             '"missing_skills": ["K8s"], "verdict": "Strong Match", '
                             '"reasoning": "good fit"}')
        r = explain_match("resume", "jd")
        assert r["overall_score"] == 82.5
        assert r["verdict"] == "Strong Match"
        assert "Python" in r["matched_skills"]

    def test_invalid_returns_none(self, fake_llm, db_session):
        fake_llm.response = "not json"
        assert explain_match("resume", "jd") is None
