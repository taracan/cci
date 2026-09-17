"""Ollama LLM client — calls a local Ollama server's /api/generate."""
from __future__ import annotations

import requests

from config import OLLAMA_BASE_URL, LLM_TIMEOUT_SECS, LLM_TEMPERATURE
from llm.base import LLMClient, LlmResult


class OllamaClient(LLMClient):
    """Talks to a local Ollama server (default http://localhost:11434)."""

    def generate(self, prompt: str) -> LlmResult:
        """Generate a completion via Ollama's /api/generate endpoint.

        Args:
            prompt: The full prompt text.

        Returns:
            An :class:`LlmResult` with generated text and token counts.

        Raises:
            requests.RequestException: On network/timeout errors.
            RuntimeError: If Ollama returns a non-200 response.
        """
        url = f"{OLLAMA_BASE_URL}/api/generate"
        payload = {
            "model": self.model_id,
            "prompt": prompt,
            "stream": False,
            "options": {"temperature": LLM_TEMPERATURE},
        }
        resp = requests.post(url, json=payload, timeout=LLM_TIMEOUT_SECS)
        if resp.status_code != 200:
            raise RuntimeError(f"Ollama {resp.status_code}: {resp.text[:200]}")

        data = resp.json()
        return LlmResult(
            text=data.get("response", ""),
            prompt_tokens=data.get("prompt_eval_count", 0),
            completion_tokens=data.get("eval_count", 0),
            model_id=self.model_id,
        )
