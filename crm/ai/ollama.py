"""Client HTTP minimal pour Ollama (chat streaming + health)."""

from __future__ import annotations

import json
import logging
from typing import Any, Generator
from urllib import error as urlerror
from urllib import request as urlrequest

from crm.ai.config import (
    OLLAMA_BASE_URL,
    OLLAMA_CONTEXT_TOKENS,
    OLLAMA_FALLBACK_MODEL,
    OLLAMA_MODEL,
    OLLAMA_NUM_PREDICT,
    OLLAMA_STREAM_TIMEOUT,
    OLLAMA_TEMPERATURE,
)

logger = logging.getLogger(__name__)


class OllamaError(RuntimeError):
    """Erreur Ollama propagée à l'API HTTP avec un message explicite."""


def health() -> dict[str, Any]:
    """Renvoie l'état du démon Ollama et la liste des modèles installés."""
    try:
        req = urlrequest.Request(f"{OLLAMA_BASE_URL}/api/tags", method="GET")
        with urlrequest.urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read().decode("utf-8") or "{}")
    except (urlerror.URLError, TimeoutError, OSError) as exc:
        return {
            "ok": False,
            "reachable": False,
            "base_url": OLLAMA_BASE_URL,
            "error": str(exc),
            "configured_model": OLLAMA_MODEL,
            "models": [],
        }
    models = [m.get("name") for m in (data.get("models") or []) if m.get("name")]
    has_primary = OLLAMA_MODEL in models
    has_fallback = OLLAMA_FALLBACK_MODEL in models
    return {
        "ok": True,
        "reachable": True,
        "base_url": OLLAMA_BASE_URL,
        "configured_model": OLLAMA_MODEL,
        "fallback_model": OLLAMA_FALLBACK_MODEL,
        "has_primary_model": has_primary,
        "has_fallback_model": has_fallback,
        "models": models,
    }


def _pick_model(preferred: str | None = None) -> str:
    """Choisit un modèle installé : preferred → primaire → fallback → premier dispo."""
    info = health()
    installed = info.get("models") or []
    for candidate in (preferred, OLLAMA_MODEL, OLLAMA_FALLBACK_MODEL):
        if candidate and candidate in installed:
            return candidate
    if installed:
        return installed[0]
    # Aucun modèle installé : on renvoie quand même le principal pour message d'erreur clair.
    return OLLAMA_MODEL


def chat_stream(
    messages: list[dict[str, str]],
    *,
    model: str | None = None,
    temperature: float | None = None,
    num_predict: int | None = None,
) -> Generator[dict[str, Any], None, None]:
    """Streame les réponses Ollama ligne par ligne (NDJSON).

    Chaque yield est un dict JSON : `{"message": {"content": "..."}, "done": bool, ...}`.
    Les exceptions réseau / décodage sont converties en `OllamaError` avec un
    message lisible — utile pour afficher quelque chose côté UI.
    """
    chosen = _pick_model(model)
    body = {
        "model": chosen,
        "messages": messages,
        "stream": True,
        "options": {
            "temperature": temperature if temperature is not None else OLLAMA_TEMPERATURE,
            "num_predict": num_predict if num_predict is not None else OLLAMA_NUM_PREDICT,
            "num_ctx": OLLAMA_CONTEXT_TOKENS,
        },
    }
    data = json.dumps(body).encode("utf-8")
    req = urlrequest.Request(
        f"{OLLAMA_BASE_URL}/api/chat",
        data=data,
        method="POST",
        headers={"Content-Type": "application/json"},
    )
    try:
        with urlrequest.urlopen(req, timeout=OLLAMA_STREAM_TIMEOUT) as resp:
            for raw_line in resp:
                if not raw_line:
                    continue
                try:
                    chunk = json.loads(raw_line.decode("utf-8"))
                except json.JSONDecodeError:
                    logger.warning("Ollama chunk illisible: %r", raw_line[:120])
                    continue
                yield chunk
                if chunk.get("done"):
                    break
    except urlerror.HTTPError as exc:
        body_txt = ""
        try:
            body_txt = exc.read().decode("utf-8")[:500]
        except Exception:
            pass
        raise OllamaError(
            f"Ollama HTTP {exc.code} ({chosen}) : {body_txt or exc.reason}"
        ) from exc
    except urlerror.URLError as exc:
        raise OllamaError(
            f"Ollama injoignable sur {OLLAMA_BASE_URL}. Lancez `ollama serve` puis "
            f"`ollama pull {chosen}`."
        ) from exc
    except TimeoutError as exc:
        raise OllamaError("Ollama : délai dépassé — modèle peut-être trop gros pour cette machine.") from exc
