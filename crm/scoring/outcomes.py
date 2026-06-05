"""Suivi des outcomes CRM et calibrage des poids."""

from __future__ import annotations

import json
from typing import Any

from crm.scoring.weights import (
    OUTCOME_PIPELINE_MAP,
    calibrate_from_outcome,
    load_agency_weights,
    save_agency_weights,
)


def pipeline_to_outcome(pipeline: str | None) -> str | None:
    if not pipeline:
        return None
    return OUTCOME_PIPELINE_MAP.get(str(pipeline).strip().lower())


def record_lead_outcome(
    conn,
    *,
    lead_id: int,
    agency_id: str,
    outcome_type: str,
    agent_id: str | None = None,
    notes: str | None = None,
    scores_snapshot: dict[str, Any] | None = None,
) -> int:
    now = __import__("datetime").datetime.now(__import__("datetime").timezone.utc).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )
    snap = json.dumps(scores_snapshot or {}, ensure_ascii=False)
    cur = conn.execute(
        """INSERT INTO lead_outcomes
           (lead_id, agency_id, outcome_type, outcome_at, agent_id, notes, scores_snapshot)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (lead_id, agency_id, outcome_type, now, agent_id, notes, snap),
    )
    return int(cur.lastrowid or 0)


def calibrate_agency_weights_from_outcome(
    conn,
    agency_id: str,
    outcome_type: str,
    scores_snapshot: dict[str, Any] | None,
) -> None:
    if not scores_snapshot:
        return
    keys = [f.get("key") for f in scores_snapshot.get("positive_factors") or [] if f.get("key")]
    tags = scores_snapshot.get("tags") or []
    tag_map = {
        "sans_agence": "sans_agence",
        "ancienne": "ancienne_45",
        "baisse_prix": "baisse_prix",
        "baisse_recente": "baisse_recente",
        "multi_baisse": "multi_baisse",
        "dvf_sous_marche": "dvf_sous",
        "sureval_opportunite": "sureval_opportunite",
        "motivation_vendeur": "motivation_texte",
        "nouveau": "nouveau",
    }
    for t in tags:
        if t in tag_map:
            keys.append(tag_map[t])
    keys = list(dict.fromkeys(keys))
    if not keys:
        return
    weights = load_agency_weights(conn, agency_id)
    new_weights = calibrate_from_outcome(weights, outcome_type, keys)
    save_agency_weights(conn, agency_id, new_weights)
