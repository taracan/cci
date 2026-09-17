"""Unit tests for llm.factory — the model-registry status gate.

The factory must only hand out a client for a model whose registry status is
'active'. Unknown / inactive / retired models must raise ModelUnavailableError
so a request can be rejected cleanly.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest

from llm.factory import resolve_model, get_llm_client, ModelUnavailableError
from llm.ollama_client import OllamaClient


class TestResolveModel:
    def test_active_model_resolves(self, seeded_models):
        row = resolve_model("llama3.2:latest")
        assert row.model_id == "llama3.2:latest"
        assert row.status == "active"

    def test_none_falls_back_to_default_active(self, seeded_models):
        # DEFAULT_LLM_MODEL is llama3.2:latest which is active in the fixture.
        row = resolve_model(None)
        assert row.model_id == "llama3.2:latest"

    def test_inactive_model_raises(self, seeded_models):
        with pytest.raises(ModelUnavailableError) as exc:
            resolve_model("retired-model")
        assert "not active" in str(exc.value)

    def test_unknown_model_falls_back_to_default(self, seeded_models):
        # An unknown id falls back to the default, which is active → resolves.
        row = resolve_model("does-not-exist")
        assert row.model_id == "llama3.2:latest"

    def test_empty_registry_raises(self, db_session):
        # No models seeded → even the default is missing.
        with pytest.raises(ModelUnavailableError):
            resolve_model("llama3.2:latest")


class TestGetLlmClient:
    def test_active_returns_ollama_client(self, seeded_models):
        client = get_llm_client("llama3.2:latest")
        assert isinstance(client, OllamaClient)
        assert client.model_id == "llama3.2:latest"

    def test_inactive_raises(self, seeded_models):
        with pytest.raises(ModelUnavailableError):
            get_llm_client("retired-model")
