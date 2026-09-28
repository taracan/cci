"""Tests for the Ollama client request (no real Ollama server needed)."""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from llm import ollama_client  # noqa: E402
from llm.ollama_client import OllamaClient  # noqa: E402


class _FakeResponse:
    status_code = 200
    text = ""

    def json(self):
        return {"response": '{"skills": ["Python"]}', "prompt_eval_count": 12, "eval_count": 7}


@pytest.fixture()
def captured(monkeypatch):
    """Replace requests.post and capture what the client sends."""
    calls = {}

    def fake_post(url, json=None, timeout=None):
        calls.update(url=url, payload=json, timeout=timeout)
        return _FakeResponse()

    monkeypatch.setattr(ollama_client.requests, "post", fake_post)
    return calls


class TestOllamaRequest:
    def test_requests_json_mode(self, captured):
        OllamaClient("llama3.2:latest").generate("Return JSON")
        assert captured["payload"]["format"] == "json"

    def test_payload_basics_unchanged(self, captured):
        OllamaClient("llama3.2:latest").generate("hello")
        p = captured["payload"]
        assert p["model"] == "llama3.2:latest" and p["prompt"] == "hello"
        assert p["stream"] is False and "temperature" in p["options"]
        assert captured["url"].endswith("/api/generate")

    def test_result_parsed(self, captured):
        r = OllamaClient("llama3.2:latest").generate("x")
        assert r.text == '{"skills": ["Python"]}'
        assert (r.prompt_tokens, r.completion_tokens, r.model_id) == (12, 7, "llama3.2:latest")
