"""Fournisseurs d'IA — abstraction commune (Ollama + APIs hébergées).

L'agent UI ne connaît que `get_provider().chat_stream(...)` et
`get_provider().health()`. Le choix concret se fait via la variable
d'environnement `AI_PROVIDER` (cf. `crm/ai/config.py`).
"""

from __future__ import annotations

from crm.ai.config import AI_PROVIDER
from crm.ai.providers.base import AIProvider, AIProviderError


def get_provider() -> AIProvider:
    """Renvoie l'instance du fournisseur configuré."""
    name = AI_PROVIDER
    if name == "ollama":
        from crm.ai.providers.ollama_provider import OllamaProvider

        return OllamaProvider()
    if name in {"groq", "openai", "mistral", "openrouter", "together"}:
        from crm.ai.providers.openai_compat import OpenAICompatProvider

        return OpenAICompatProvider(name)
    raise AIProviderError(f"AI_PROVIDER inconnu : {name!r}")


__all__ = ["AIProvider", "AIProviderError", "get_provider"]
