"""Client HTTP minimal pour Ollama (chat streaming + health) basé sur `requests`."""

from __future__ import annotations

import json
import logging
from typing import Any, Generator

import requests

from crm.ai.config import (
    OLLAMA_API_KEY,
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


def _auth_headers() -> dict[str, str]:
    """Headers HTTP avec Bearer token si le reverse-proxy en exige un."""
    if OLLAMA_API_KEY:
        return {"Authorization": f"Bearer {OLLAMA_API_KEY}"}
    return {}


def health() -> dict[str, Any]:
    """Renvoie l'état du démon Ollama et la liste des modèles installés."""
    try:
        resp = requests.get(
            f"{OLLAMA_BASE_URL}/api/tags",
            headers=_auth_headers(),
            timeout=5,
        )
    except requests.RequestException as exc:
        return {
            "ok": False,
            "reachable": False,
            "base_url": OLLAMA_BASE_URL,
            "error": str(exc),
            "configured_model": OLLAMA_MODEL,
            "models": [],
        }
    if resp.status_code in (401, 403):
        return {
            "ok": False,
            "reachable": True,
            "base_url": OLLAMA_BASE_URL,
            "error": f"HTTP {resp.status_code} — clé OLLAMA_API_KEY correcte ?",
            "configured_model": OLLAMA_MODEL,
            "models": [],
            "needs_auth": True,
        }
    if not resp.ok:
        return {
            "ok": False,
            "reachable": True,
            "base_url": OLLAMA_BASE_URL,
            "error": f"HTTP {resp.status_code} : {resp.text[:200]}",
            "configured_model": OLLAMA_MODEL,
            "models": [],
        }
    try:
        data = resp.json()
    except ValueError:
        data = {}
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
    return OLLAMA_MODEL


def chat_stream(
    messages: list[dict[str, str]],
    *,
    model: str | None = None,
    temperature: float | None = None,
    num_predict: int | None = None,
) -> Generator[dict[str, Any], None, None]:
    """Streame les réponses Ollama ligne par ligne (NDJSON)."""
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
    try:
        resp = requests.post(
            f"{OLLAMA_BASE_URL}/api/chat",
            json=body,
            headers={**_auth_headers(), "Content-Type": "application/json"},
            stream=True,
            timeout=OLLAMA_STREAM_TIMEOUT,
        )
    except requests.RequestException as exc:
        is_remote = OLLAMA_BASE_URL.startswith("https://") or not OLLAMA_BASE_URL.startswith(
            ("http://127.", "http://localhost")
        )
        if is_remote:
            raise OllamaError(
                f"Ollama injoignable sur {OLLAMA_BASE_URL}. Vérifiez que le VPS "
                "est en ligne et que le reverse-proxy écoute en HTTPS."
            ) from exc
        raise OllamaError(
            f"Ollama injoignable sur {OLLAMA_BASE_URL}. Lancez `ollama serve` puis "
            f"`ollama pull {chosen}`."
        ) from exc

    if resp.status_code in (401, 403):
        try:
            resp.close()
        except Exception:
            pass
        raise OllamaError(
            f"Ollama refuse la requête (HTTP {resp.status_code}). Vérifiez "
            "OLLAMA_API_KEY côté app et la clé attendue par votre reverse-proxy."
        )
    if not resp.ok:
        body_txt = ""
        try:
            body_txt = resp.text[:400]
        finally:
            try:
                resp.close()
            except Exception:
                pass
        raise OllamaError(f"Ollama HTTP {resp.status_code} ({chosen}) : {body_txt or 'erreur inconnue'}")

    try:
        for raw_line in resp.iter_lines(decode_unicode=True):
            if not raw_line:
                continue
            line = (raw_line or "").strip()
            if not line:
                continue
            try:
                chunk = json.loads(line)
            except json.JSONDecodeError:
                logger.warning("Ollama chunk illisible: %r", line[:160])
                continue
            yield chunk
            if chunk.get("done"):
                return
    finally:
        try:
            resp.close()
        except Exception:
            pass
