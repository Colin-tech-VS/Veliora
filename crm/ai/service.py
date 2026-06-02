"""Orchestration de l'assistant : assemble contexte + historique et streame Ollama."""

from __future__ import annotations

import logging
import re
from typing import Generator

from crm.ai.config import MAX_HISTORY_MESSAGES
from crm.ai.context import build_system_prompt, trim_messages_for_model
from crm.ai.ollama import OllamaError, chat_stream
from crm.ai.storage import (
    append_message,
    create_conversation,
    get_conversation,
    get_messages,
    rename_conversation,
)

logger = logging.getLogger(__name__)

_TITLE_FALLBACK = "Nouvelle conversation"


def _auto_title(user_text: str) -> str:
    t = (user_text or "").strip().replace("\n", " ")
    if not t:
        return _TITLE_FALLBACK
    t = re.sub(r"\s+", " ", t)
    return (t[:60] + "…") if len(t) > 60 else t


def ensure_conversation(
    agency_id: str,
    conv_id: str | None,
    *,
    user_id: str | None = None,
    user_first_text: str | None = None,
) -> dict:
    """Crée la conversation si elle n'existe pas, sinon la renvoie."""
    if conv_id:
        conv = get_conversation(conv_id, agency_id)
        if conv:
            return conv
    title = _auto_title(user_first_text or "")
    return create_conversation(agency_id, user_id=user_id, title=title)


def stream_assistant_reply(
    agency_id: str,
    conversation_id: str,
    user_message: str,
    *,
    user_first_name: str | None = None,
) -> Generator[dict, None, None]:
    """Streame la réponse Ollama et persiste user + assistant.

    Yields des événements `{type, ...}` consommés par l'endpoint SSE :
    - `start`   : début du stream, l'UI peut afficher le pseudo "écrit…"
    - `token`   : un fragment de texte à concaténer
    - `final`   : tout est fini, contient le texte complet + l'id assistant_msg
    - `error`   : message d'erreur
    """
    # Persiste tout de suite le tour utilisateur — comme ça si le stream
    # plante en cours de route, l'historique reste cohérent.
    user_msg = append_message(conversation_id, agency_id, "user", user_message)

    # Renomme la conversation au premier vrai message utilisateur.
    history = get_messages(conversation_id, agency_id)
    if len([m for m in history if m["role"] == "user"]) == 1:
        rename_conversation(conversation_id, agency_id, _auto_title(user_message))

    yield {"type": "start", "user_message_id": user_msg["id"]}

    system_prompt = build_system_prompt(agency_id, user_first_name=user_first_name)
    messages_for_model: list[dict] = [{"role": "system", "content": system_prompt}]
    for m in history:
        if m["role"] in ("user", "assistant"):
            messages_for_model.append({"role": m["role"], "content": m["content"]})
    messages_for_model = trim_messages_for_model(messages_for_model, MAX_HISTORY_MESSAGES)

    full_text_parts: list[str] = []
    try:
        for chunk in chat_stream(messages_for_model):
            piece = (chunk.get("message") or {}).get("content") or ""
            if piece:
                full_text_parts.append(piece)
                yield {"type": "token", "delta": piece}
            if chunk.get("done"):
                break
    except OllamaError as exc:
        msg = str(exc)
        yield {"type": "error", "error": msg}
        # On enregistre quand même un message assistant marqué error pour le debug
        append_message(
            conversation_id,
            agency_id,
            "assistant",
            f"⚠️ {msg}",
            meta={"error": True},
        )
        return
    except Exception as exc:
        logger.exception("ollama stream failed")
        yield {"type": "error", "error": f"Erreur IA inattendue : {exc}"}
        return

    full_text = "".join(full_text_parts).strip() or "(réponse vide)"
    assistant_msg = append_message(conversation_id, agency_id, "assistant", full_text)
    yield {
        "type": "final",
        "assistant_message_id": assistant_msg["id"],
        "content": full_text,
    }
