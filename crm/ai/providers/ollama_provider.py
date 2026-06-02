"""Wrapper provider autour de l'implémentation Ollama historique."""

from __future__ import annotations

from typing import Any, Generator

from crm.ai import ollama as legacy
from crm.ai.config import OLLAMA_BASE_URL, OLLAMA_MODEL
from crm.ai.providers.base import AIProvider, AIProviderError


class OllamaProvider(AIProvider):
    name = "ollama"

    def health(self) -> dict[str, Any]:
        info = legacy.health()
        # Uniformise les clés avec le provider OpenAI-compat (l'UI lit `provider`,
        # `label`, etc.).
        info["provider"] = "ollama"
        info["label"] = "Ollama"
        info.setdefault("configured_model", OLLAMA_MODEL)
        info.setdefault("base_url", OLLAMA_BASE_URL)
        return info

    def chat_stream(
        self,
        messages: list[dict[str, str]],
        *,
        model: str | None = None,
        temperature: float | None = None,
        num_predict: int | None = None,
    ) -> Generator[dict[str, Any], None, None]:
        try:
            yield from legacy.chat_stream(
                messages,
                model=model,
                temperature=temperature,
                num_predict=num_predict,
            )
        except legacy.OllamaError as exc:
            raise AIProviderError(str(exc)) from exc
