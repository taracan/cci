"""Abstract LLM client contract.

Any provider (Ollama now; Bedrock/OpenAI later) implements generate(). Routes
and services depend only on this interface, never a concrete provider.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass
class LlmResult:
    """Result of a single LLM generation call.

    Attributes:
        text: The raw text the model produced.
        prompt_tokens: Approximate tokens in the prompt (0 if unknown).
        completion_tokens: Approximate tokens generated (0 if unknown).
        model_id: The model that produced this result.
    """
    text: str
    prompt_tokens: int = 0
    completion_tokens: int = 0
    model_id: str = ""


class LLMClient(ABC):
    """Interface every LLM provider must implement."""

    def __init__(self, model_id: str) -> None:
        """Store the model id this client will call.

        Args:
            model_id: The provider-specific model identifier.
        """
        self.model_id = model_id

    @abstractmethod
    def generate(self, prompt: str) -> LlmResult:
        """Generate a completion for the given prompt.

        Args:
            prompt: The full prompt text.

        Returns:
            An :class:`LlmResult` with the generated text and token counts.
        """
        raise NotImplementedError
