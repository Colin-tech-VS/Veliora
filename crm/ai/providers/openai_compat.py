"""Fournisseur unique pour toutes les APIs compatibles OpenAI chat completions.

Groq, Mistral, OpenAI, OpenRouter, Together, DeepInfra… exposent toutes la
même surface `POST /chat/completions` avec stream SSE. On les supporte avec
une seule classe paramétrée par `name`.

Format de chunk renvoyé en sortie : strictement le même que la classe
Ollama (`{"message": {"content": "..."}, "done": bool}`) pour ne pas
toucher au reste de la stack (service.py / app.py / ai.js).

Implémentation : on utilise `requests` (déjà en dépendance) plutôt que
`urllib` parce que `requests.iter_lines(decode_unicode=True)` gère
proprement le streaming chunked HTTP, le buffering et l'encodage — c'est
beaucoup plus fiable pour SSE.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Generator

import requests

from crm.ai.config import (
    AI_API_KEY,
    AI_BASE_URL,
    AI_MODEL,
    OLLAMA_NUM_PREDICT,
    OLLAMA_STREAM_TIMEOUT,
    OLLAMA_TEMPERATURE,
)
from crm.ai.providers.base import AIProvider, AIProviderError

logger = logging.getLogger(__name__)


# Métadonnées par fournisseur : URL par défaut, modèle par défaut, lien doc clé.
_PROVIDERS: dict[str, dict[str, str]] = {
    "groq": {
        "base_url": "https://api.groq.com/openai/v1",
        "default_model": "llama-3.3-70b-versatile",
        "key_url": "https://console.groq.com/keys",
        "label": "Groq",
    },
    "openai": {
        "base_url": "https://api.openai.com/v1",
        "default_model": "gpt-4o-mini",
        "key_url": "https://platform.openai.com/api-keys",
        "label": "OpenAI",
    },
    "mistral": {
        "base_url": "https://api.mistral.ai/v1",
        "default_model": "mistral-small-latest",
        "key_url": "https://console.mistral.ai/api-keys/",
        "label": "Mistral",
    },
    "openrouter": {
        "base_url": "https://openrouter.ai/api/v1",
        "default_model": "meta-llama/llama-3.3-70b-instruct:free",
        "key_url": "https://openrouter.ai/keys",
        "label": "OpenRouter",
    },
    "together": {
        "base_url": "https://api.together.xyz/v1",
        "default_model": "meta-llama/Meta-Llama-3.1-70B-Instruct-Turbo",
        "key_url": "https://api.together.ai/settings/api-keys",
        "label": "Together AI",
    },
}


class OpenAICompatProvider(AIProvider):
    def __init__(self, name: str):
        if name not in _PROVIDERS:
            raise AIProviderError(f"Fournisseur OpenAI-compat inconnu : {name}")
        self.name = name
        meta = _PROVIDERS[name]
        self._base_url = (AI_BASE_URL or meta["base_url"]).rstrip("/")
        self._default_model = AI_MODEL or meta["default_model"]
        self._label = meta["label"]
        self._key_url = meta["key_url"]

    def _ensure_key(self) -> None:
        if not AI_API_KEY:
            raise AIProviderError(
                f"{self._label} : variable d'environnement AI_API_KEY manquante. "
                f"Génère une clé sur {self._key_url} puis "
                f"`scalingo env-set AI_API_KEY=<clé>`."
            )

    def _auth_headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {AI_API_KEY}"}

    # ── Health ───────────────────────────────────────────────────────────
    def health(self) -> dict[str, Any]:
        """Tente un GET /models pour confirmer que la clé fonctionne."""
        if not AI_API_KEY:
            return {
                "ok": False,
                "reachable": False,
                "provider": self.name,
                "base_url": self._base_url,
                "configured_model": self._default_model,
                "label": self._label,
                "error": (
                    f"AI_API_KEY manquante pour {self._label}. "
                    f"Crée une clé sur {self._key_url}."
                ),
                "key_url": self._key_url,
                "models": [],
            }
        try:
            resp = requests.get(
                f"{self._base_url}/models",
                headers=self._auth_headers(),
                timeout=10,
            )
        except requests.RequestException as exc:
            return {
                "ok": False,
                "reachable": False,
                "provider": self.name,
                "base_url": self._base_url,
                "configured_model": self._default_model,
                "label": self._label,
                "error": f"{self._label} injoignable : {exc}",
                "key_url": self._key_url,
                "models": [],
            }
        if resp.status_code in (401, 403):
            return {
                "ok": False,
                "reachable": True,
                "provider": self.name,
                "base_url": self._base_url,
                "configured_model": self._default_model,
                "label": self._label,
                "error": f"{self._label} HTTP {resp.status_code} — clé valide ?",
                "needs_auth": True,
                "key_url": self._key_url,
                "models": [],
            }
        if not resp.ok:
            return {
                "ok": False,
                "reachable": True,
                "provider": self.name,
                "base_url": self._base_url,
                "configured_model": self._default_model,
                "label": self._label,
                "error": f"{self._label} HTTP {resp.status_code} : {resp.text[:200]}",
                "key_url": self._key_url,
                "models": [],
            }
        try:
            data = resp.json()
        except ValueError:
            data = {}
        items = data.get("data") if isinstance(data, dict) else None
        models = [m.get("id") for m in (items or []) if isinstance(m, dict) and m.get("id")]
        return {
            "ok": True,
            "reachable": True,
            "provider": self.name,
            "base_url": self._base_url,
            "configured_model": self._default_model,
            "label": self._label,
            "models": models[:30],
            "has_primary_model": (self._default_model in models) if models else True,
        }

    # ── Streaming chat ───────────────────────────────────────────────────
    def chat_stream(
        self,
        messages: list[dict[str, str]],
        *,
        model: str | None = None,
        temperature: float | None = None,
        num_predict: int | None = None,
    ) -> Generator[dict[str, Any], None, None]:
        self._ensure_key()
        chosen = (model or self._default_model).strip()
        payload = {
            "model": chosen,
            "messages": messages,
            "stream": True,
            "temperature": (
                temperature if temperature is not None else OLLAMA_TEMPERATURE
            ),
            "max_tokens": (
                num_predict if num_predict is not None else OLLAMA_NUM_PREDICT
            ),
        }

        try:
            resp = requests.post(
                f"{self._base_url}/chat/completions",
                headers={
                    **self._auth_headers(),
                    "Content-Type": "application/json",
                    "Accept": "text/event-stream",
                },
                json=payload,
                stream=True,
                timeout=OLLAMA_STREAM_TIMEOUT,
            )
        except requests.RequestException as exc:
            raise AIProviderError(
                f"{self._label} injoignable sur {self._base_url} : {exc}"
            ) from exc

        if resp.status_code in (401, 403):
            try:
                resp.close()
            except Exception:
                pass
            raise AIProviderError(
                f"{self._label} refuse la requête (HTTP {resp.status_code}). "
                f"Clé AI_API_KEY invalide ou révoquée. "
                f"Crée une nouvelle clé sur {self._key_url}."
            )
        if resp.status_code == 429:
            body = ""
            try:
                body = resp.text[:200]
            finally:
                try:
                    resp.close()
                except Exception:
                    pass
            raise AIProviderError(
                f"{self._label} : quota atteint (HTTP 429). {body or 'Réessayez dans quelques minutes.'}"
            )
        if not resp.ok:
            body = ""
            try:
                body = resp.text[:300]
            finally:
                try:
                    resp.close()
                except Exception:
                    pass
            raise AIProviderError(
                f"{self._label} HTTP {resp.status_code} ({chosen}) : {body or 'erreur inconnue'}"
            )

        try:
            yield from self._iter_sse(resp)
        finally:
            try:
                resp.close()
            except Exception:
                pass

    @staticmethod
    def _iter_sse(resp) -> Generator[dict[str, Any], None, None]:
        """Parse un flux SSE `data: {...}` et le re-mappe au format Ollama-like.

        Les providers OpenAI-compatibles streament :
            data: {"choices": [{"delta": {"content": "..."}}]}\\n\\n
            data: [DONE]\\n\\n
        """
        for raw_line in resp.iter_lines(decode_unicode=True):
            if raw_line is None:
                continue
            line = (raw_line or "").strip()
            if not line:
                continue
            if not line.startswith("data:"):
                # Certains providers (OpenRouter, Together) intercalent des
                # commentaires SSE qui commencent par `: ` — on les ignore.
                continue
            payload = line[5:].strip()
            if payload == "[DONE]":
                yield {"message": {"content": ""}, "done": True}
                return
            try:
                obj = json.loads(payload)
            except json.JSONDecodeError:
                logger.warning("SSE chunk illisible: %r", payload[:160])
                continue
            choices = obj.get("choices") or []
            if not choices:
                # Certains chunks ne contiennent que des `usage` ou metadata.
                continue
            delta = (choices[0] or {}).get("delta") or {}
            piece = delta.get("content") or ""
            finish = choices[0].get("finish_reason")
            yield {"message": {"content": piece}, "done": bool(finish)}
            if finish:
                return
