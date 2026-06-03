"""Progression transaction (côté acquéreur + jalons) et commissions.

Tables dédiées (jamais d'ALTER sur `leads`) :
- `transaction_progress` : jalons hors mandat (acquéreur rapproché, dossier
  acquéreur, compromis, vente). Une ligne par (agency_id, lead_id).
- `transaction_commissions` : commission encaissée à la vente, répartie
  agence / agent.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from crawler.storage import get_connection

DEFAULT_AGENT_COMMISSION_PCT = 30.0


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def ensure_transaction_tables(conn) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS transaction_progress (
            agency_id        TEXT NOT NULL,
            lead_id          INTEGER NOT NULL,
            buyer_client_id  TEXT,
            buyer_dossier_id TEXT,
            visit_at         TEXT,
            compromis_at     TEXT,
            sold_at          TEXT,
            updated_at       TEXT NOT NULL,
            PRIMARY KEY (agency_id, lead_id)
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS transaction_commissions (
            id             TEXT PRIMARY KEY,
            agency_id      TEXT NOT NULL,
            lead_id        INTEGER,
            mandate_id     TEXT,
            agent_id       TEXT,
            agent_name     TEXT,
            total_amount   REAL NOT NULL DEFAULT 0,
            agent_pct      REAL NOT NULL DEFAULT 0,
            agent_amount   REAL NOT NULL DEFAULT 0,
            agency_amount  REAL NOT NULL DEFAULT 0,
            created_at     TEXT NOT NULL
        )
        """
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_tx_commissions_agency "
        "ON transaction_commissions(agency_id, agent_id)"
    )


def get_progress(agency_id: str, lead_id: int) -> dict:
    with get_connection() as conn:
        ensure_transaction_tables(conn)
        row = conn.execute(
            "SELECT * FROM transaction_progress WHERE agency_id = ? AND lead_id = ?",
            (agency_id, int(lead_id)),
        ).fetchone()
    return dict(row) if row else {}


def get_progress_map(agency_id: str) -> dict[int, dict]:
    with get_connection() as conn:
        ensure_transaction_tables(conn)
        rows = conn.execute(
            "SELECT * FROM transaction_progress WHERE agency_id = ?",
            (agency_id,),
        ).fetchall()
    return {int(r["lead_id"]): dict(r) for r in rows}


def set_progress(agency_id: str, lead_id: int, **fields) -> dict:
    allowed = {"buyer_client_id", "buyer_dossier_id", "visit_at", "compromis_at", "sold_at"}
    updates = {k: v for k, v in fields.items() if k in allowed}
    if not updates:
        return get_progress(agency_id, lead_id)
    current = get_progress(agency_id, lead_id)
    merged = {**{k: current.get(k) for k in allowed}, **updates}
    now = _now()
    with get_connection() as conn:
        ensure_transaction_tables(conn)
        conn.execute(
            """INSERT INTO transaction_progress
               (agency_id, lead_id, buyer_client_id, buyer_dossier_id,
                visit_at, compromis_at, sold_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(agency_id, lead_id) DO UPDATE SET
                   buyer_client_id = excluded.buyer_client_id,
                   buyer_dossier_id = excluded.buyer_dossier_id,
                   visit_at = excluded.visit_at,
                   compromis_at = excluded.compromis_at,
                   sold_at = excluded.sold_at,
                   updated_at = excluded.updated_at""",
            (
                agency_id,
                int(lead_id),
                merged.get("buyer_client_id"),
                merged.get("buyer_dossier_id"),
                merged.get("visit_at"),
                merged.get("compromis_at"),
                merged.get("sold_at"),
                now,
            ),
        )
        conn.commit()
    return get_progress(agency_id, lead_id)


def record_commission(
    agency_id: str,
    *,
    lead_id: int | None,
    mandate_id: str | None,
    agent_id: str | None,
    agent_name: str | None,
    total_amount: float,
    agent_pct: float | None = None,
) -> dict:
    pct = DEFAULT_AGENT_COMMISSION_PCT if agent_pct is None else max(0.0, min(100.0, float(agent_pct)))
    total = max(0.0, float(total_amount or 0))
    agent_amount = round(total * pct / 100.0, 2)
    agency_amount = round(total - agent_amount, 2)
    cid = str(uuid.uuid4())
    now = _now()
    with get_connection() as conn:
        ensure_transaction_tables(conn)
        # Idempotent par lead : on remplace une commission déjà saisie pour ce bien.
        if lead_id is not None:
            conn.execute(
                "DELETE FROM transaction_commissions WHERE agency_id = ? AND lead_id = ?",
                (agency_id, int(lead_id)),
            )
        conn.execute(
            """INSERT INTO transaction_commissions
               (id, agency_id, lead_id, mandate_id, agent_id, agent_name,
                total_amount, agent_pct, agent_amount, agency_amount, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                cid,
                agency_id,
                int(lead_id) if lead_id is not None else None,
                mandate_id,
                agent_id,
                agent_name,
                total,
                pct,
                agent_amount,
                agency_amount,
                now,
            ),
        )
        conn.commit()
    return {
        "id": cid,
        "total_amount": total,
        "agent_pct": pct,
        "agent_amount": agent_amount,
        "agency_amount": agency_amount,
    }


def list_commissions(agency_id: str) -> dict:
    with get_connection() as conn:
        ensure_transaction_tables(conn)
        rows = conn.execute(
            "SELECT * FROM transaction_commissions WHERE agency_id = ? ORDER BY created_at DESC",
            (agency_id,),
        ).fetchall()
    items = [dict(r) for r in rows]
    total = round(sum(r["total_amount"] for r in items), 2)
    agency_total = round(sum(r["agency_amount"] for r in items), 2)
    by_agent: dict[str, dict] = {}
    for r in items:
        aid = r.get("agent_id") or "—"
        slot = by_agent.setdefault(
            aid, {"agent_id": r.get("agent_id"), "agent_name": r.get("agent_name") or "Agence", "deals": 0, "agent_amount": 0.0}
        )
        slot["deals"] += 1
        slot["agent_amount"] = round(slot["agent_amount"] + r["agent_amount"], 2)
    return {
        "commissions": items,
        "total_amount": total,
        "agency_amount": agency_total,
        "agent_amount": round(total - agency_total, 2),
        "by_agent": list(by_agent.values()),
        "deals_count": len(items),
    }
