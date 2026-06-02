"""Fournisseur unique pour toutes les APIs compatibles OpenAI chat completions.

Groq, Mistral, OpenAI, OpenRouter, Together, DeepInfra… exposent toutes la
même surface `POST /chat/completions` avec stream SSE. On les supporte avec
une seule classe paramétrée par `name`.

Format de chunk renvoyé en sortie : strictement le même que la classe
Ollama (`{"message": {"content": "..."}, "done": bool}`) pour ne pas
toucher au reste de la stack (service.py / app.py / ai.js).
"""

from __future__ import annotations

import json
import logging
from typing import Any, Generator
from urllib import error as urlerror
from urllib import request as urlrequest

from crm.ai.config import (
    AI_API_KEY,
    AI_BASE_URL,
    AI_MODEL,
    OLLAMA_CONTEXT_TOKENS,
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
        # Modèle gratuit par défaut sur OpenRouter
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

    # ── Helpers HTTP ────────────────────────────────────────────────────
    def _headers(self) -> dict[str, str]:
        if not AI_API_KEY:
            raise AIProviderError(
                f"{self._label} : variable d'environnement AI_API_KEY manquante. "
                f"Génère une clé sur {self._key_url} puis "
                f"`scalingo env-set AI_API_KEY=<clé>`."
            )
        return {
            "Authorization": f"Bearer {AI_API_KEY}",
            "Content-Type": "application/json",
            "Accept": "text/event-stream",
        }

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
                "error": (
                    f"AI_API_KEY manquante pour {self._label}. "
                    f"Crée une clé sur {self._key_url}."
                ),
                "key_url": self._key_url,
                "models": [],
            }
        try:
            req = urlrequest.Request(
                f"{self._base_url}/models",
                method="GET",
                headers={"Authorization": f"Bearer {AI_API_KEY}"},
            )
            with urlrequest.urlopen(req, timeout=10) as resp:
                data = json.loads(resp.read().decode("utf-8") or "{}")
        except urlerror.HTTPError as exc:
            return {
                "ok": False,
                "reachable": True,
                "provider": self.name,
                "base_url": self._base_url,
                "configured_model": self._default_model,
                "error": f"{self._label} HTTP {exc.code} ({exc.reason}) — clé valide ?",
                "needs_auth": exc.code in (401, 403),
                "key_url": self._key_url,
                "models": [],
            }
        except (urlerror.URLError, TimeoutError, OSError) as exc:
            return {
                "ok": False,
                "reachable": False,
                "provider": self.name,
                "base_url": self._base_url,
                "configured_model": self._default_model,
                "error": f"{self._label} injoignable : {exc}",
                "key_url": self._key_url,
                "models": [],
            }
        items = data.get("data") if isinstance(data, dict) else None
        models = [m.get("id") for m in (items or []) if isinstance(m, dict) and m.get("id")]
        return {
            "ok": True,
            "reachable": True,
            "provider": self.name,
            "base_url": self._base_url,
            "configured_model": self._default_model,
            "label": self._label,
            "models": models[:30],  # cap pour pas saturer l'UI
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
        # OpenAI/Groq utilisent max_tokens ; pas de num_ctx (géré côté serveur).
        # On laisse passer le contexte tel quel : les providers acceptent jusqu'à
        # 128k tokens pour les modèles récents — bien plus que notre besoin.
        _ = OLLAMA_CONTEXT_TOKENS  # silencieusement ignoré ici

        data = json.dumps(payload).encode("utf-8")
        try:
            req = urlrequest.Request(
                f"{self._base_url}/chat/completions",
                data=data,
                method="POST",
                headers=self._headers(),
            )
        except AIProviderError:
            raise

        try:
            with urlrequest.urlopen(req, timeout=OLLAMA_STREAM_TIMEOUT) as resp:
                yield from self._iter_sse(resp)
        except urlerror.HTTPError as exc:
            body_txt = ""
            try:
                body_txt = exc.read().decode("utf-8")[:500]
            except Exception:
                pass
            if exc.code in (401, 403):
                raise AIProviderError(
                    f"{self._label} refuse la requête (HTTP {exc.code}). "
                    f"Clé AI_API_KEY invalide ou révoquée. "
                    f"Crée une nouvelle clé sur {self._key_url}."
                ) from exc
            if exc.code == 429:
                raise AIProviderError(
                    f"{self._label} : quota atteint. Réessayez dans quelques minutes "
                    "ou changez de modèle."
                ) from exc
            raise AIProviderError(
                f"{self._label} HTTP {exc.code} ({chosen}) : {body_txt or exc.reason}"
            ) from exc
        except urlerror.URLError as exc:
            raise AIProviderError(
                f"{self._label} injoignable sur {self._base_url}. Vérifiez la "
                f"connexion réseau du dyno."
            ) from exc
        except TimeoutError as exc:
            raise AIProviderError(
                f"{self._label} : délai dépassé sur le streaming."
            ) from exc

    @staticmethod
    def _iter_sse(resp) -> Generator[dict[str, Any], None, None]:
        """Parse un flux SSE `data: {...}` et le re-mappe au format Ollama-like.

        Les providers OpenAI-compatibles streament :
            data: {"choices": [{"delta": {"content": "..."}}]}\\n\\n
            data: [DONE]\\n\\n
        """
        for raw_line in resp:
            if not raw_line:
                continue
            line = raw_line.decode("utf-8", errors="ignore").strip()
            if not line or not line.startswith("data:"):
                continue
            payload = line[5:].strip()
            if payload == "[DONE]":
                yield {"message": {"content": ""}, "done": True}
                break
            try:
                obj = json.loads(payload)
            except json.JSONDecodeError:
                logger.warning("OpenAI-compat chunk illisible: %r", payload[:120])
                continue
            choices = obj.get("choices") or []
            if not choices:
                continue
            delta = (choices[0] or {}).get("delta") or {}
            piece = delta.get("content") or ""
            finish = choices[0].get("finish_reason")
            yield {"message": {"content": piece}, "done": bool(finish)}
            if finish:
                break
