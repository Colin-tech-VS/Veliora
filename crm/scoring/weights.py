"""Poids nationaux par défaut et calibrage par agence (±30 %)."""

from __future__ import annotations

import json
from typing import Any

# Multiplicateurs sur les points de base (1.0 = défaut national).
NATIONAL_DEFAULT_WEIGHTS: dict[str, float] = {
    "sans_agence": 1.0,
    "ancienne_15": 1.0,
    "ancienne_30": 1.0,
    "ancienne_45": 1.0,
    "ancienne_60": 1.0,
    "baisse_prix": 1.0,
    "multi_baisse": 1.0,
    "dvf_sous": 1.0,
    "dvf_leger": 1.0,
    "dvf_aligne": 1.0,
    "contact_phone": 1.0,
    "contact_email": 1.0,
    "vente": 1.0,
    "bien_cible": 1.0,
    "demande": 1.0,
    "nouveau": 1.0,
    "malus_agence": 1.0,
    "malus_sur_marche": 1.0,
}

WEIGHT_MIN = 0.70
WEIGHT_MAX = 1.30

OUTCOME_PIPELINE_MAP = {
    "contacte": "call",
    "rdv": "rdv",
    "mandat": "mandat_signe",
    "perdu": "mandat_perdu",
}


def merge_weights(stored: dict[str, float] | None) -> dict[str, float]:
    out = dict(NATIONAL_DEFAULT_WEIGHTS)
    if stored:
        for k, v in stored.items():
            if k in out and isinstance(v, (int, float)):
                out[k] = max(WEIGHT_MIN, min(WEIGHT_MAX, float(v)))
    return out


def apply_weight(base_points: int, key: str, weights: dict[str, float]) -> int:
    w = weights.get(key, 1.0)
    return int(round(base_points * w))


def calibrate_from_outcome(
    weights: dict[str, float],
    outcome_type: str,
    factor_keys: list[str],
) -> dict[str, float]:
    """Ajuste les poids après un outcome terrain (facteurs visibles uniquement)."""
    out = dict(weights)
    positive_outcomes = {"call", "rdv", "mandat_signe"}
    negative_outcomes = {"refuse", "mandat_perdu"}

    for key in factor_keys:
        if key not in out:
            continue
        if outcome_type in positive_outcomes:
            out[key] = min(WEIGHT_MAX, out[key] + 0.03)
        elif outcome_type in negative_outcomes:
            out[key] = max(WEIGHT_MIN, out[key] - 0.02)
    return out


def load_agency_weights(conn, agency_id: str) -> dict[str, float]:
    row = conn.execute(
        "SELECT weights_json FROM agency_scoring_weights WHERE agency_id = ?",
        (agency_id,),
    ).fetchone()
    if not row or not row["weights_json"]:
        return merge_weights(None)
    try:
        stored = json.loads(row["weights_json"])
    except (json.JSONDecodeError, TypeError):
        stored = None
    return merge_weights(stored if isinstance(stored, dict) else None)


def save_agency_weights(conn, agency_id: str, weights: dict[str, float]) -> None:
    now = __import__("datetime").datetime.now(__import__("datetime").timezone.utc).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )
    payload = json.dumps(weights, ensure_ascii=False)
    conn.execute(
        """INSERT INTO agency_scoring_weights (agency_id, weights_json, updated_at)
           VALUES (?, ?, ?)
           ON CONFLICT(agency_id) DO UPDATE SET
           weights_json = excluded.weights_json,
           updated_at = excluded.updated_at""",
        (agency_id, payload, now),
    )
