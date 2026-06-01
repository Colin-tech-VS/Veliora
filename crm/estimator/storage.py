"""Persistance de l'estimation de prix sur le lead (cohérence inter-onglets)."""

from __future__ import annotations

import json
import logging
import time
from datetime import datetime, timezone

from crawler.storage import get_connection
from velora_db.config import is_postgres

logger = logging.getLogger(__name__)

_SCHEMA_READY = False
_LAST_FAIL = 0.0
_RETRY_COOLDOWN = 300.0  # 5 min : évite de marteler un ALTER bloqué par un verrou


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _columns_present(conn) -> bool:
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
    else:
        cols = {r[1] for r in conn.execute("PRAGMA table_info(leads)").fetchall()}
    return "price_estimate" in cols and "price_estimate_at" in cols


def ensure_estimate_schema() -> bool:
    """Garantit price_estimate + price_estimate_at sur leads. Renvoie True si prêt.

    Ne lève jamais : sur PostgreSQL, l'ALTER peut être bloqué par un verrou
    (UPDATE leads concurrents) et dépasser le statement_timeout. Dans ce cas on
    renvoie False (la persistance est simplement différée) ; la migration au
    démarrage (postgres_schema.sql) ajoute proprement les colonnes hors charge.
    """
    global _SCHEMA_READY, _LAST_FAIL
    if _SCHEMA_READY:
        return True
    if _LAST_FAIL and (time.monotonic() - _LAST_FAIL) < _RETRY_COOLDOWN:
        return False
    try:
        with get_connection() as conn:
            if _columns_present(conn):
                _SCHEMA_READY = True
                return True
            if is_postgres():
                # IF NOT EXISTS + lock_timeout court : on échoue vite plutôt que
                # de consommer tout le statement_timeout en attendant le verrou.
                conn.execute("SET LOCAL lock_timeout = '3s'")
                conn.execute("ALTER TABLE leads ADD COLUMN IF NOT EXISTS price_estimate TEXT")
                conn.execute("ALTER TABLE leads ADD COLUMN IF NOT EXISTS price_estimate_at TEXT")
            else:
                lcols = {r[1] for r in conn.execute("PRAGMA table_info(leads)").fetchall()}
                if lcols:
                    if "price_estimate" not in lcols:
                        conn.execute("ALTER TABLE leads ADD COLUMN price_estimate TEXT")
                    if "price_estimate_at" not in lcols:
                        conn.execute("ALTER TABLE leads ADD COLUMN price_estimate_at TEXT")
            conn.commit()
        _SCHEMA_READY = True
        return True
    except Exception as exc:
        _LAST_FAIL = time.monotonic()
        logger.warning("ensure_estimate_schema différé (verrou/timeout): %s", str(exc)[:160])
        return False


def save_lead_estimate(lead_id: int, agency_id: str, estimate: dict) -> str | None:
    """Enregistre la dernière estimation sur le lead. Renvoie l'horodatage ISO, ou None."""
    if not estimate or not estimate.get("ok"):
        return None
    if not ensure_estimate_schema():
        # Schéma pas encore prêt : on n'échoue pas, la persistance est différée.
        return None
    at = _now()
    payload = json.dumps(estimate, ensure_ascii=False)
    try:
        with get_connection() as conn:
            conn.execute(
                """
                UPDATE leads SET price_estimate = ?, price_estimate_at = ?, updated_at = ?
                WHERE id = ? AND agency_id = ?
                """,
                (payload, at, at, lead_id, agency_id),
            )
            conn.commit()
    except Exception as exc:
        logger.warning("save_lead_estimate %s différé: %s", lead_id, str(exc)[:160])
        return None
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
