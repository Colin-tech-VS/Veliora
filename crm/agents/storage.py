"""Prise en charge des annonces par agent + portefeuille.

Un « agent » est un utilisateur de l'agence (`agency_users`). La prise en charge
relie un prospect/annonce détecté à un agent : c'est la condition pour publier
l'annonce sur le portail public. Le portefeuille d'un agent = les annonces qu'il
gère (prises en charge + publiées).

Comme pour `lead_estimates` / `lead_address_matches`, on n'ajoute PAS de colonne
à `leads` (ALTER = verrou lourd). Table dédiée `lead_assignments`.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from crawler.storage import get_connection

logger = logging.getLogger(__name__)

_SCHEMA_READY = False


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def ensure_agents_schema(conn) -> None:
    global _SCHEMA_READY
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS lead_assignments (
            agency_id   TEXT NOT NULL,
            lead_id     INTEGER NOT NULL,
            agent_id    TEXT NOT NULL,
            agent_name  TEXT,
            assigned_at TEXT NOT NULL,
            PRIMARY KEY (agency_id, lead_id)
        )
        """
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_lead_assignments_agent "
        "ON lead_assignments(agency_id, agent_id)"
    )
    _SCHEMA_READY = True


def agent_display_name(first: str | None, last: str | None, email: str | None) -> str:
    name = " ".join(p.strip() for p in (first, last) if p and p.strip())
    return name or (email or "").split("@")[0] or "Agent"


def list_agents(agency_id: str, *, with_portfolio: bool = True) -> list[dict]:
    """Collaborateurs actifs de l'agence + (option) compteurs de portefeuille."""
    if not agency_id:
        return []
    with get_connection() as conn:
        ensure_agents_schema(conn)
        rows = conn.execute(
            """SELECT id, email, first_name, last_name, role
               FROM agency_users
               WHERE agency_id = ? AND active = 1
               ORDER BY (role = 'admin') DESC, first_name, last_name""",
            (agency_id,),
        ).fetchall()
        assigned_counts: dict[str, int] = {}
        if with_portfolio:
            for r in conn.execute(
                """SELECT agent_id, COUNT(*) AS n FROM lead_assignments
                   WHERE agency_id = ? GROUP BY agent_id""",
                (agency_id,),
            ).fetchall():
                assigned_counts[r["agent_id"]] = r["n"]

        published_counts: dict[str, int] = {}
        if with_portfolio:
            try:
                from crm.portal.storage import ensure_portal_tables

                ensure_portal_tables(conn)
                for r in conn.execute(
                    """SELECT agent_id, COUNT(*) AS n FROM portal_listings
                       WHERE agency_id = ? AND agent_id IS NOT NULL AND status = 'published'
                       GROUP BY agent_id""",
                    (agency_id,),
                ).fetchall():
                    published_counts[r["agent_id"]] = r["n"]
            except Exception:
                logger.debug("portfolio publié indisponible", exc_info=True)

    agents = []
    for r in rows:
        aid = r["id"]
        agents.append(
            {
                "id": aid,
                "email": r["email"],
                "first_name": r["first_name"],
                "last_name": r["last_name"],
                "role": r["role"],
                "name": agent_display_name(r["first_name"], r["last_name"], r["email"]),
                "assigned_count": assigned_counts.get(aid, 0),
                "published_count": published_counts.get(aid, 0),
            }
        )
    return agents


def _agent_in_agency(conn, agency_id: str, agent_id: str) -> dict | None:
    row = conn.execute(
        """SELECT id, email, first_name, last_name FROM agency_users
           WHERE id = ? AND agency_id = ? AND active = 1""",
        (agent_id, agency_id),
    ).fetchone()
    return dict(row) if row else None


def assign_lead(agency_id: str, lead_id: int, agent_id: str) -> dict:
    """Prend en charge un prospect au nom d'un agent de l'agence."""
    if not agency_id or not agent_id:
        return {"ok": False, "error": "Agence et agent requis."}
    with get_connection() as conn:
        ensure_agents_schema(conn)
        agent = _agent_in_agency(conn, agency_id, agent_id)
        if not agent:
            return {"ok": False, "error": "Agent introuvable dans votre agence."}
        name = agent_display_name(agent["first_name"], agent["last_name"], agent["email"])
        conn.execute(
            """INSERT INTO lead_assignments (agency_id, lead_id, agent_id, agent_name, assigned_at)
               VALUES (?, ?, ?, ?, ?)
               ON CONFLICT(agency_id, lead_id) DO UPDATE SET
                   agent_id = excluded.agent_id,
                   agent_name = excluded.agent_name,
                   assigned_at = excluded.assigned_at""",
            (agency_id, int(lead_id), agent_id, name, _now()),
        )
        conn.commit()
    return {"ok": True, "agent_id": agent_id, "agent_name": name}


def unassign_lead(agency_id: str, lead_id: int) -> bool:
    with get_connection() as conn:
        ensure_agents_schema(conn)
        cur = conn.execute(
            "DELETE FROM lead_assignments WHERE agency_id = ? AND lead_id = ?",
            (agency_id, int(lead_id)),
        )
        conn.commit()
        return cur.rowcount > 0


def get_assignment(agency_id: str, lead_id: int) -> dict | None:
    with get_connection() as conn:
        ensure_agents_schema(conn)
        row = conn.execute(
            "SELECT agent_id, agent_name, assigned_at FROM lead_assignments "
            "WHERE agency_id = ? AND lead_id = ?",
            (agency_id, int(lead_id)),
        ).fetchone()
    return dict(row) if row else None


def get_assignments_map(agency_id: str) -> dict[int, dict]:
    """Toutes les prises en charge de l'agence, indexées par lead_id (hydratation liste)."""
    if not agency_id:
        return {}
    with get_connection() as conn:
        ensure_agents_schema(conn)
        rows = conn.execute(
            "SELECT lead_id, agent_id, agent_name, assigned_at "
            "FROM lead_assignments WHERE agency_id = ?",
            (agency_id,),
        ).fetchall()
    return {
        int(r["lead_id"]): {
            "agent_id": r["agent_id"],
            "agent_name": r["agent_name"],
            "assigned_at": r["assigned_at"],
        }
        for r in rows
    }
