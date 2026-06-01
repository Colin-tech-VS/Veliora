"""Persistance de l'estimation de prix sur le lead (cohérence inter-onglets)."""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone

from crawler.storage import get_connection
from velora_db.config import is_postgres

logger = logging.getLogger(__name__)

_SCHEMA_READY = False


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def ensure_estimate_schema() -> None:
    """Ajoute price_estimate (JSON) + price_estimate_at sur leads (idempotent, 2 backends)."""
    global _SCHEMA_READY
    if _SCHEMA_READY:
        return
    with get_connection() as conn:
        if is_postgres():
            cur = conn.execute(
                """
                SELECT column_name FROM information_schema.columns
                WHERE table_name = 'leads'
                  AND column_name IN ('price_estimate', 'price_estimate_at')
                """
            )
            cols = {
                r[0] if isinstance(r, (tuple, list)) else r["column_name"]
                for r in cur.fetchall()
            }
            if "price_estimate" not in cols:
                conn.execute("ALTER TABLE leads ADD COLUMN price_estimate TEXT")
            if "price_estimate_at" not in cols:
                conn.execute("ALTER TABLE leads ADD COLUMN price_estimate_at TEXT")
        else:
            lcols = {r[1] for r in conn.execute("PRAGMA table_info(leads)").fetchall()}
            if lcols:
                if "price_estimate" not in lcols:
                    conn.execute("ALTER TABLE leads ADD COLUMN price_estimate TEXT")
                if "price_estimate_at" not in lcols:
                    conn.execute("ALTER TABLE leads ADD COLUMN price_estimate_at TEXT")
        conn.commit()
    _SCHEMA_READY = True


def save_lead_estimate(lead_id: int, agency_id: str, estimate: dict) -> str | None:
    """Enregistre la dernière estimation sur le lead. Renvoie l'horodatage ISO."""
    if not estimate or not estimate.get("ok"):
        return None
    ensure_estimate_schema()
    at = _now()
    payload = json.dumps(estimate, ensure_ascii=False)
    with get_connection() as conn:
        conn.execute(
            """
            UPDATE leads SET price_estimate = ?, price_estimate_at = ?, updated_at = ?
            WHERE id = ? AND agency_id = ?
            """,
            (payload, at, at, lead_id, agency_id),
        )
        conn.commit()
    return at


def parse_lead_estimate(raw) -> dict | None:
    """Décode la colonne price_estimate (JSON) en dict, ou None."""
    if not raw:
        return None
    if isinstance(raw, dict):
        return raw
    try:
        data = json.loads(raw)
        return data if isinstance(data, dict) else None
    except (json.JSONDecodeError, TypeError):
        return None
