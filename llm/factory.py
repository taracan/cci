"""LLM client factory with model-registry status gating.

Before returning a client, this checks the DB model_registry:
  - the model must exist
  - its status must be 'active' (not inactive/retired)
Otherwise it raises ModelUnavailableError so the caller can reject the request.
"""
from __future__ import annotations

from typing import Optional

from config import DEFAULT_LLM_MODEL, LLM_PROVIDER
from db.database import get_session
from db.models_orm import ModelRegistry
from llm.base import LLMClient
from llm.ollama_client import OllamaClient


class ModelUnavailableError(Exception):
    """Raised when a requested model is missing or not active."""


def resolve_model(model_id: Optional[str]) -> ModelRegistry:
    """Look up a model in the registry and verify it is usable.

    Args:
        model_id: Requested model id, or None to use the active default.

    Returns:
        The matching ModelRegistry row (detached copy of key fields).

    Raises:
        ModelUnavailableError: If the model is unknown or not 'active'.
    """
    with get_session() as s:
        row = None
        if model_id:
            row = s.query(ModelRegistry).filter_by(model_id=model_id).first()
        if row is None:
            row = s.query(ModelRegistry).filter_by(model_id=DEFAULT_LLM_MODEL).first()

        if row is None:
            raise ModelUnavailableError(f"Model '{model_id or DEFAULT_LLM_MODEL}' not in registry")
        if row.status != "active":
            raise ModelUnavailableError(
                f"Model '{row.model_id}' is '{row.status}', not active"
            )
        # Return a lightweight detached object (copy fields we need).
        detached = ModelRegistry(
            model_id=row.model_id, family=row.family, provider=row.provider,
            purpose=row.purpose, status=row.status,
        )
        return detached


def get_llm_client(model_id: Optional[str] = None) -> LLMClient:
    """Return an LLM client for an active model (status-gated).

    Args:
        model_id: Requested model id, or None for the active default.

    Returns:
        A concrete :class:`LLMClient` (currently Ollama).

    Raises:
        ModelUnavailableError: If the model is unknown or not active.
        ValueError: If the provider is unsupported.
    """
    model = resolve_model(model_id)
    provider = model.provider or LLM_PROVIDER
    if provider == "ollama":
        return OllamaClient(model.model_id)
    raise ValueError(f"Unsupported LLM provider: {provider}")
