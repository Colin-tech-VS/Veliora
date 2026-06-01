"""Recalcul batch des scores pour une agence."""

from __future__ import annotations


def recalc_agency_lead_scores(agency_id: str, *, limit: int = 5000) -> int:
    """Recalcule Score Mandat et explication pour tous les leads actifs.

    Contexte préchargé une fois (poids + historiques + clients) -> la demande
    (acheteurs/locataires compatibles) est intégrée au score sans N requêtes.
    """
    from crawler.storage import get_connection, persist_lead_scores
    from crm.scoring.recalc import enrich_lead_scores, load_scoring_batch_context

    n = 0
    with get_connection() as conn:
        rows = conn.execute(
            """SELECT * FROM leads WHERE agency_id = ? AND status != 'retire'
               ORDER BY updated_at DESC LIMIT ?""",
            (agency_id, limit),
        ).fetchall()
    bases = []
    for row in rows:
        keys = row.keys()
        base = {k: row[k] for k in keys}
        if base.get("score_explanation") and isinstance(base["score_explanation"], str):
            import json

            try:
                base["score_explanation"] = json.loads(base["score_explanation"])
            except json.JSONDecodeError:
                pass
        bases.append(base)

    ctx = load_scoring_batch_context(
        agency_id, [int(b["id"]) for b in bases if b.get("id")]
    )
    for base in bases:
        enriched = enrich_lead_scores(base, scoring_ctx=ctx)
        persist_lead_scores(int(base["id"]), agency_id, enriched)
        n += 1
    return n


def schedule_agency_rescore(agency_id: str) -> None:
    """Re-scoring en arrière-plan (ex. après modif des profils acheteurs/locataires)."""
    import threading

    if not agency_id:
        return

    def _run() -> None:
        try:
            recalc_agency_lead_scores(agency_id)
        except Exception:
            import logging

            logging.getLogger(__name__).exception("schedule_agency_rescore")

    threading.Thread(target=_run, daemon=True, name="rescore").start()
