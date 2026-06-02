"""Interface commune à tous les fournisseurs d'IA."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Generator


class AIProviderError(RuntimeError):
    """Erreur fournisseur, propagée à l'API HTTP avec un message lisible."""


class AIProvider(ABC):
    """Contrat minimal qu'un fournisseur d'IA doit respecter."""

    #: Identifiant court (`ollama`, `groq`, `mistral`…). Renvoyé dans /health.
    name: str = "abstract"

    @abstractmethod
    def health(self) -> dict[str, Any]:
        """Diagnostic du fournisseur — modèle actif, joignabilité, hints."""

    @abstractmethod
    def chat_stream(
        self,
        messages: list[dict[str, str]],
        *,
        model: str | None = None,
        temperature: float | None = None,
        num_predict: int | None = None,
    ) -> Generator[dict[str, Any], None, None]:
        """Streame des chunks au format `{"message": {"content": "..."}, "done": bool}`."""
