"""Persistance du rapprochement d'adresse — table dédiée `lead_address_matches`.

Comme pour `lead_estimates`, on N'ajoute PAS de colonne à `leads` (un ALTER y
exige un verrou ACCESS EXCLUSIVE qui entre en conflit avec les UPDATE du crawl).
Table séparée créée via CREATE TABLE IF NOT EXISTS, rattachée au lead à la
lecture. Le détail complet (candidats + raisons) vit en JSON dans `payload` ;
`probable_address` et `confidence` sont dénormalisés pour tri/filtre rapides.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone

from crawler.storage import get_connection

logger = logging.getLogger(__name__)

_SCHEMA_READY = False


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def ensure_address_schema() -> bool:
    global _SCHEMA_READY
    if _SCHEMA_READY:
        return True
    try:
        with get_connection() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS lead_address_matches (
                    lead_id          INTEGER NOT NULL,
                    agency_id        TEXT NOT NULL,
                    probable_address TEXT,
                    confidence       INTEGER NOT NULL DEFAULT 0,
                    payload          TEXT NOT NULL,
                    updated_at       TEXT NOT NULL,
                    PRIMARY KEY (lead_id, agency_id)
                )
                """
            )
            # Caractéristiques structurées de l'annonce (DPE, pièces, année…),
            # rangées hors `leads` pour éviter tout ALTER/verrou.
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS lead_features (
                    lead_id    INTEGER NOT NULL,
                    agency_id  TEXT NOT NULL,
                    payload    TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY (lead_id, agency_id)
                )
                """
            )
            conn.commit()
        _SCHEMA_READY = True
        return True
    except Exception as exc:
        logger.warning("ensure_address_schema: %s", str(exc)[:160])
        return False


def save_lead_features(lead_id: int, agency_id: str, features: dict) -> str | None:
    """Persiste les `ListingFeatures` (dict) d'un lead. Idempotent."""
    if not features or not ensure_address_schema():
        return None
    at = _now()
    payload = json.dumps(features, ensure_ascii=False)
    try:
        with get_connection() as conn:
            conn.execute(
                """
                INSERT INTO lead_features (lead_id, agency_id, payload, updated_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(lead_id, agency_id) DO UPDATE SET
                    payload = excluded.payload,
                    updated_at = excluded.updated_at
                """,
                (lead_id, agency_id, payload, at),
            )
            conn.commit()
    except Exception as exc:
        logger.warning("save_lead_features %s différé: %s", lead_id, str(exc)[:160])
        return None
    return at


def get_lead_features(lead_id: int, agency_id: str) -> dict | None:
    if not ensure_address_schema():
        return None
    try:
        with get_connection() as conn:
            row = conn.execute(
                "SELECT payload FROM lead_features WHERE lead_id = ? AND agency_id = ?",
                (lead_id, agency_id),
            ).fetchone()
    except Exception:
        return None
    if not row:
        return None
    payload = row[0] if isinstance(row, (tuple, list)) else row["payload"]
    return _parse(payload)


def save_address_match(lead_id: int, agency_id: str, resolution: dict) -> str | None:
    if not resolution or not resolution.get("ok"):
        return None
    if not ensure_address_schema():
        return None
    at = _now()
    payload = json.dumps(resolution, ensure_ascii=False)
    confidence = int(resolution.get("score_confiance") or 0)
    probable = resolution.get("adresse_probable")
    try:
        with get_connection() as conn:
            conn.execute(
                """
                INSERT INTO lead_address_matches
                    (lead_id, agency_id, probable_address, confidence, payload, updated_at)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(lead_id, agency_id) DO UPDATE SET
                    probable_address = excluded.probable_address,
                    confidence = excluded.confidence,
                    payload = excluded.payload,
                    updated_at = excluded.updated_at
                """,
                (lead_id, agency_id, probable, confidence, payload, at),
            )
            conn.commit()
    except Exception as exc:
        logger.warning("save_address_match %s différé: %s", lead_id, str(exc)[:160])
        return None
    return at


def _parse(payload) -> dict | None:
    if not payload:
        return None
    if isinstance(payload, dict):
        return payload
    try:
        data = json.loads(payload)
        return data if isinstance(data, dict) else None
    except (json.JSONDecodeError, TypeError):
        return None


def get_address_match(lead_id: int, agency_id: str) -> tuple[dict | None, str | None]:
    if not ensure_address_schema():
        return None, None
    try:
        with get_connection() as conn:
            row = conn.execute(
                "SELECT payload, updated_at FROM lead_address_matches "
                "WHERE lead_id = ? AND agency_id = ?",
                (lead_id, agency_id),
            ).fetchone()
    except Exception:
        return None, None
    if not row:
        return None, None
    if isinstance(row, (tuple, list)):
        return _parse(row[0]), row[1]
    return _parse(row["payload"]), row["updated_at"]
