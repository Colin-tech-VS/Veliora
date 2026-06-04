"""Actions explicites exécutables côté serveur après validation par l'utilisateur.

L'IA ne modifie rien d'elle-même : elle suggère un bloc `ACTION_JSON` dans sa
réponse ; le client envoie cette action séparément après confirmation manuelle.
On garde donc la main : aucune action destructrice ne peut partir « par erreur ».
"""

from __future__ import annotations

import logging
import re

logger = logging.getLogger(__name__)

ALLOWED_ACTIONS = {
    "update_pipeline",
    "add_note",
    "set_followup",
    "remember",
}

_LEAD_ID_IN_TEXT = re.compile(
    r"(?:#|prospect\s*#?|lead[_\s-]?id\s*[:=]\s*)(\d{1,8})\b",
    re.I,
)


def _resolve_lead_id(action: dict) -> int | None:
    """lead_id explicite ou déduit du texte (#60, prospect 60…)."""
    if not isinstance(action, dict):
        return None
    for key in ("lead_id", "prospect_id", "id"):
        raw = action.get(key)
        if raw is None or raw == "":
            continue
        try:
            n = int(str(raw).strip().lstrip("#"))
            if n > 0:
                return n
        except (TypeError, ValueError):
            continue
    for field in ("note", "content", "message", "label", "title"):
        text = (action.get(field) or "").strip()
        if not text:
            continue
        m = _LEAD_ID_IN_TEXT.search(text)
        if m:
            try:
                return int(m.group(1))
            except ValueError:
                pass
    return None


def execute_action(agency_id: str, action: dict) -> dict:
    """Applique une action validée par l'agent. Retourne {ok, detail}."""
    if not isinstance(action, dict):
        return {"ok": False, "error": "Action invalide"}
    name = (action.get("action") or "").strip().lower()
    if name not in ALLOWED_ACTIONS:
        return {"ok": False, "error": f"Action non reconnue : {name or '∅'}"}

    if name == "update_pipeline":
        return _update_pipeline(agency_id, action)
    if name == "add_note":
        return _add_note(agency_id, action)
    if name == "set_followup":
        return _set_followup(agency_id, action)
    if name == "remember":
        return _remember(agency_id, action)
    return {"ok": False, "error": "Action non implémentée"}


def _update_pipeline(agency_id: str, action: dict) -> dict:
    from crawler.storage import patch_lead

    lead_id = _resolve_lead_id(action)
    if not lead_id:
        return {
            "ok": False,
            "error": "lead_id manquant — indiquez l'id prospect (#60) dans l'action JSON",
        }
    pipeline = (action.get("pipeline") or "").strip().lower()
    if pipeline not in {"nouveau", "a_contacter", "contacte", "rdv", "mandat", "perdu"}:
        return {"ok": False, "error": f"pipeline inconnu : {pipeline}"}
    try:
        lead = patch_lead(lead_id, agency_id, {"pipeline": pipeline})
    except Exception as exc:
        logger.exception("AI update_pipeline failed")
        return {"ok": False, "error": str(exc)}
    if not lead:
        return {"ok": False, "error": "Prospect introuvable"}
    return {"ok": True, "detail": f"Pipeline #{lead_id} → {pipeline}", "lead": lead}


def _add_note(agency_id: str, action: dict) -> dict:
    from crawler.storage import get_lead, patch_lead

    lead_id = _resolve_lead_id(action)
    if not lead_id:
        return {
            "ok": False,
            "error": "lead_id manquant — indiquez l'id prospect (#60) dans l'action JSON",
        }
    note = (action.get("note") or "").strip()
    if not note:
        return {"ok": False, "error": "note vide"}
    lead = get_lead(lead_id, agency_id)
    if not lead:
        return {"ok": False, "error": "Prospect introuvable"}
    existing = (lead.get("notes") or "").strip()
    from datetime import datetime, timezone

    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M")
    new_note = f"[IA {stamp}] {note}"
    combined = (existing + "\n" + new_note).strip() if existing else new_note
    try:
        updated = patch_lead(lead_id, agency_id, {"notes": combined})
    except Exception as exc:
        logger.exception("AI add_note failed")
        return {"ok": False, "error": str(exc)}
    return {"ok": True, "detail": f"Note ajoutée — prospect #{lead_id}", "lead": updated}


def _set_followup(agency_id: str, action: dict) -> dict:
    from crawler.storage import patch_lead

    lead_id = _resolve_lead_id(action)
    if not lead_id:
        return {
            "ok": False,
            "error": "lead_id manquant — indiquez l'id prospect (#60) dans l'action JSON",
        }
    when = (action.get("date") or action.get("when") or "").strip()
    if not when:
        return {"ok": False, "error": "date manquante"}
    try:
        updated = patch_lead(lead_id, agency_id, {"next_follow_up": when})
    except Exception as exc:
        logger.exception("AI set_followup failed")
        return {"ok": False, "error": str(exc)}
    return {"ok": True, "detail": f"Relance #{lead_id} le {when}", "lead": updated}


def _remember(agency_id: str, action: dict) -> dict:
    from crm.ai.storage import add_memory

    content = (action.get("content") or action.get("note") or "").strip()
    if not content:
        return {"ok": False, "error": "contenu mémoire vide"}
    scope = (action.get("scope") or "general").strip().lower()
    mem = add_memory(agency_id, content, scope=scope, source="assistant")
    return {"ok": True, "detail": "Mémorisé", "memory": mem}
