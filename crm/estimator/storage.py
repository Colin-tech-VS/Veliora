"""Persistance de l'estimation de prix — table dédiée lead_estimates.

Conception : on N'ajoute PAS de colonne à la table `leads` (un ALTER y exige un
verrou ACCESS EXCLUSIVE qui entre en conflit avec les UPDATE leads du crawl /
géocodage → statement_timeout sur PostgreSQL). On utilise une table séparée,
créée via CREATE TABLE IF NOT EXISTS (aucun verrou sur leads), et on rattache
l'estimation au lead à la lecture.
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


def ensure_estimate_schema() -> bool:
    """Crée la table lead_estimates si besoin (léger, sans verrou sur leads)."""
    global _SCHEMA_READY
    if _SCHEMA_READY:
        return True
    try:
        with get_connection() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS lead_estimates (
                    lead_id INTEGER NOT NULL,
                    agency_id TEXT NOT NULL,
                    payload TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY (lead_id, agency_id)
                )
                """
            )
            conn.commit()
        _SCHEMA_READY = True
        return True
    except Exception as exc:
        logger.warning("ensure_estimate_schema: %s", str(exc)[:160])
        return False


def save_lead_estimate(lead_id: int, agency_id: str | None, estimate: dict) -> str | None:
    """Enregistre/écrase l'estimation du lead. Renvoie l'horodatage ISO, ou None."""
    if not estimate or not estimate.get("ok"):
        return None
    if not ensure_estimate_schema():
        return None
    at = _now()
    payload = json.dumps(estimate, ensure_ascii=False)
    try:
        with get_connection() as conn:
            conn.execute(
                """
                INSERT INTO lead_estimates (lead_id, agency_id, payload, updated_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(lead_id, agency_id) DO UPDATE SET
                    payload = excluded.payload,
                    updated_at = excluded.updated_at
                """,
                (lead_id, agency_id, payload, at),
            )
            conn.commit()
    except Exception as exc:
        logger.warning("save_lead_estimate %s différé: %s", lead_id, str(exc)[:160])
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


def get_lead_estimate(lead_id: int, agency_id: str) -> tuple[dict | None, str | None]:
    if not ensure_estimate_schema():
        return None, None
    try:
        with get_connection() as conn:
            row = conn.execute(
                """SELECT payload, updated_at FROM lead_estimates
                   WHERE lead_id = ?
                   AND (agency_id IS NULL OR TRIM(COALESCE(agency_id, '')) = '' OR agency_id = ?)
                   ORDER BY CASE WHEN agency_id IS NULL OR TRIM(COALESCE(agency_id, '')) = '' THEN 0 ELSE 1 END
                   LIMIT 1""",
                (lead_id, agency_id),
            ).fetchone()
    except Exception:
        return None, None
    if not row:
        return None, None
    payload = row["payload"] if not isinstance(row, (tuple, list)) else row[0]
    at = row["updated_at"] if not isinstance(row, (tuple, list)) else row[1]
    return _parse(payload), at


def get_estimates_for_lead_ids(lead_ids: list[int]) -> dict[int, tuple[dict | None, str | None]]:
    """Estimations du pool partagé pour une liste de fiches."""
    if not lead_ids or not ensure_estimate_schema():
        return {}
    out: dict[int, tuple[dict | None, str | None]] = {}
    placeholders = ",".join("?" * len(lead_ids))
    try:
        with get_connection() as conn:
            rows = conn.execute(
                f"""SELECT lead_id, payload, updated_at FROM lead_estimates
                   WHERE lead_id IN ({placeholders})
                   AND (agency_id IS NULL OR TRIM(COALESCE(agency_id, '')) = '')""",
                tuple(int(i) for i in lead_ids),
            ).fetchall()
    except Exception:
        return {}
    for r in rows:
        if isinstance(r, (tuple, list)):
            lid, payload, at = r[0], r[1], r[2]
        else:
            lid, payload, at = r["lead_id"], r["payload"], r["updated_at"]
        out[int(lid)] = (_parse(payload), at)
    return out


def get_estimates_for_agency(agency_id: str) -> dict[int, tuple[dict | None, str | None]]:
    """Toutes les estimations d'une agence en une requête (rattachement liste)."""
    if not ensure_estimate_schema():
        return {}
    out: dict[int, tuple[dict | None, str | None]] = {}
    try:
        with get_connection() as conn:
            rows = conn.execute(
                "SELECT lead_id, payload, updated_at FROM lead_estimates WHERE agency_id = ?",
                (agency_id,),
            ).fetchall()
    except Exception:
        return {}
    for r in rows:
        if isinstance(r, (tuple, list)):
            lid, payload, at = r[0], r[1], r[2]
        else:
            lid, payload, at = r["lead_id"], r["payload"], r["updated_at"]
        out[int(lid)] = (_parse(payload), at)
    return out
