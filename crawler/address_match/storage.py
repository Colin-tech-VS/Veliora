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


_ADDR_BAD = ("", "—", "-", "n/a", "non renseigné")


def apply_resolution_to_lead(lead_id: int, agency_id: str, resolution: dict) -> bool:
    """Injecte l'adresse DPE/BAN résolue dans la table `leads` (pour la carte).

    Objectif : que l'adresse réelle la plus précise (idéalement exacte) trouvée
    par le rapprochement DPE serve directement au placement du marqueur, au lieu
    de laisser la carte géocoder une ligne grossière « ville (CP) ».

    Règles (idempotentes, jamais destructrices) :
    - `latitude`/`longitude` : posées depuis le candidat le mieux classé seulement
      si le lead n'a pas encore de coordonnées (on ne remplace pas une position
      déjà connue, ex. lat/lng publiées par le portail).
    - `address` : renseignée avec l'adresse probable uniquement si l'adresse
      courante est vide, factice, ou seulement approximative (« … (approx.) »).
    """
    if not resolution or not resolution.get("ok"):
        return False
    probable = (resolution.get("adresse_probable") or "").strip()
    if not probable:
        return False

    # Coordonnées du candidat correspondant à l'adresse probable (le mieux classé).
    cands = resolution.get("candidats") or []
    top = next((c for c in cands if (c.get("adresse") or "") == probable), None)
    if top is None and cands:
        top = cands[0]
    lat = lng = None
    if top and top.get("latitude") is not None and top.get("longitude") is not None:
        try:
            lat = float(top["latitude"])
            lng = float(top["longitude"])
        except (TypeError, ValueError):
            lat = lng = None

    at = _now()
    try:
        with get_connection() as conn:
            row = conn.execute(
                "SELECT address, city, postcode, latitude, longitude FROM leads "
                "WHERE id = ? AND agency_id = ?",
                (lead_id, agency_id),
            ).fetchone()
            if not row:
                return False
            if hasattr(row, "keys"):
                cur_addr = (row["address"] or "") or ""
                row_city = row["city"]
                row_pc = row["postcode"]
                cur_lat = row["latitude"]
                cur_lng = row["longitude"]
            else:
                cur_addr = (row[0] or "") if len(row) > 0 else ""
                row_city = row[1] if len(row) > 1 else None
                row_pc = row[2] if len(row) > 2 else None
                cur_lat = row[3] if len(row) > 3 else None
                cur_lng = row[4] if len(row) > 4 else None

            sets: list[str] = []
            params: list = []
            addr_l = cur_addr.strip().lower()
            from crawler.address_quality import is_city_only_address

            from crawler.address_quality import has_approximate_address_marker

            addr_is_replaceable = (
                addr_l in _ADDR_BAD
                or has_approximate_address_marker(cur_addr)
                or is_city_only_address(cur_addr, row_city, row_pc)
            )
            if addr_is_replaceable:
                sets.append("address = ?")
                params.append(probable)
            if lat is not None and lng is not None and (cur_lat is None or cur_lng is None):
                sets.append("latitude = ?")
                sets.append("longitude = ?")
                params.append(lat)
                params.append(lng)
            if not sets:
                return False
            sets.append("updated_at = ?")
            params.append(at)
            params.extend([lead_id, agency_id])
            conn.execute(
                f"UPDATE leads SET {', '.join(sets)} WHERE id = ? AND agency_id = ?",
                params,
            )
            conn.commit()
    except Exception as exc:
        logger.warning("apply_resolution_to_lead %s différé: %s", lead_id, str(exc)[:160])
        return False
    return True


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
