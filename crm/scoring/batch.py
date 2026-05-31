"""Recalcul batch des scores pour une agence."""

from __future__ import annotations


def recalc_agency_lead_scores(agency_id: str, *, limit: int = 5000) -> int:
    """Recalcule Score Mandat et explication pour tous les leads actifs."""
    from crawler.storage import get_connection, persist_lead_scores
    from crm.scoring.recalc import enrich_lead_scores

    n = 0
    with get_connection() as conn:
        rows = conn.execute(
            """SELECT * FROM leads WHERE agency_id = ? AND status != 'retire'
               ORDER BY updated_at DESC LIMIT ?""",
            (agency_id, limit),
        ).fetchall()
    for row in rows:
        keys = row.keys()
        base = {k: row[k] for k in keys}
        if base.get("score_explanation") and isinstance(base["score_explanation"], str):
            import json

            try:
                base["score_explanation"] = json.loads(base["score_explanation"])
            except json.JSONDecodeError:
                pass
        enriched = enrich_lead_scores(base)
        persist_lead_scores(int(row["id"]), agency_id, enriched)
        n += 1
    return n
