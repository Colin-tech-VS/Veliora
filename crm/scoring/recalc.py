"""Point d'entrée — enrichissement lead avec scoring V2."""

from __future__ import annotations

import json
from typing import Any

from crm.scoring.explain import build_score_explanation
from crm.scoring.mandate import compute_mandate_score, days_since
from crm.scoring.price_history import count_price_drops_from_history
from crm.scoring.weights import merge_weights


class ScoringBatchContext:
    """Poids agence + historiques prix préchargés (évite N requêtes SQL)."""

    __slots__ = ("weights", "history_by_lead")

    def __init__(
        self,
        weights: dict[str, float],
        history_by_lead: dict[int, list[dict]],
    ) -> None:
        self.weights = weights
        self.history_by_lead = history_by_lead


def load_scoring_batch_context(agency_id: str, lead_ids: list[int]) -> ScoringBatchContext:
    weights = merge_weights(None)
    history: dict[int, list[dict]] = {}
    if not agency_id or not lead_ids:
        return ScoringBatchContext(weights, history)
    try:
        from crawler.storage import get_connection
        from crm.scoring.price_history import fetch_price_history_map
        from crm.scoring.weights import load_agency_weights

        with get_connection() as conn:
            weights = load_agency_weights(conn, str(agency_id))
            history = fetch_price_history_map(conn, str(agency_id), lead_ids)
    except Exception:
        pass
    return ScoringBatchContext(weights, history)


def _apply_scoring_context(lead: dict, ctx: ScoringBatchContext | None) -> dict:
    if ctx is None:
        return _load_scoring_context(lead)
    lead = dict(lead)
    lead_id = lead.get("id")
    if lead_id:
        rows = ctx.history_by_lead.get(int(lead_id), [])
        drops, last_pct = count_price_drops_from_history(
            rows,
            current_price=lead.get("price"),
            previous_price=lead.get("previous_price"),
        )
        lead["price_change_count"] = max(int(lead.get("price_change_count") or 0), drops)
        if last_pct is not None:
            lead["last_price_drop_pct"] = last_pct
    lead["_agency_weights"] = ctx.weights
    return lead


def _load_scoring_context(lead: dict) -> dict:
    """Charge historique prix et poids agence si lead_id présent."""
    lead_id = lead.get("id")
    agency_id = lead.get("agency_id")
    if not lead_id or not agency_id:
        return lead

    try:
        from crawler.storage import get_connection
        from crm.scoring.price_history import fetch_price_history_rows
        from crm.scoring.weights import load_agency_weights

        with get_connection() as conn:
            rows = fetch_price_history_rows(conn, int(lead_id), str(agency_id))
            drops, last_pct = count_price_drops_from_history(
                rows,
                current_price=lead.get("price"),
                previous_price=lead.get("previous_price"),
            )
            lead["price_change_count"] = max(
                int(lead.get("price_change_count") or 0),
                drops,
            )
            if last_pct is not None:
                lead["last_price_drop_pct"] = last_pct
            lead["_agency_weights"] = load_agency_weights(conn, str(agency_id))
    except Exception:
        lead.setdefault("_agency_weights", merge_weights(None))
    return lead


def enrich_lead_scores(
    lead: dict,
    *,
    scoring_ctx: ScoringBatchContext | None = None,
) -> dict:
    """Calcule Score Mandat™, explication JSON et champs dérivés."""
    lead = _apply_scoring_context(dict(lead), scoring_ctx)
    weights = lead.pop("_agency_weights", None) or merge_weights(None)

    result = compute_mandate_score(lead, weights=weights)
    explanation = build_score_explanation(lead, result)

    lead["mandate_score"] = result.score
    lead["score"] = result.score
    lead["mandate_score_reason"] = result.reason
    lead["alert_tags"] = result.tags
    lead["priority_tier"] = explanation["priority_tier"]
    lead["score_explanation"] = explanation
    lead["score_positive_factors"] = result.positive
    lead["score_negative_factors"] = result.negative

    pub_days = days_since(lead.get("published_at") or lead.get("listedAt"))
    lead["days_on_market"] = pub_days
    prev_p = lead.get("previous_price")
    cur_p = lead.get("price")
    try:
        prev_i = int(prev_p) if prev_p is not None else 0
        cur_i = int(cur_p) if cur_p is not None else 0
    except (TypeError, ValueError):
        prev_i, cur_i = 0, 0
    if prev_i > 0 and cur_i:
        lead["price_change_pct"] = round((cur_i - prev_i) / prev_i * 100, 1)
    else:
        lead["price_change_pct"] = None

    lead["_scores_enriched"] = True
    return lead


def batch_enrich_leads(leads: list[dict], agency_id: str) -> list[dict]:
    """Enrichit une liste de leads avec 1–2 requêtes SQL au lieu de N."""
    if not leads:
        return leads
    lead_ids = [int(l["id"]) for l in leads if l.get("id")]
    ctx = load_scoring_batch_context(agency_id, lead_ids)
    return [enrich_lead_scores(l, scoring_ctx=ctx) for l in leads]


def enrich_lead_row(lead: dict, *, force: bool = False) -> dict:
    """Alias compat radar / storage."""
    if not force and lead.get("_scores_enriched"):
        return lead
    return enrich_lead_scores(lead)


def scores_snapshot_from_lead(lead: dict) -> dict[str, Any]:
    expl = lead.get("score_explanation")
    if isinstance(expl, dict):
        return expl
    if isinstance(expl, str):
        try:
            return json.loads(expl)
        except json.JSONDecodeError:
            pass
    return {
        "mandate_score": lead.get("mandate_score"),
        "positive_factors": lead.get("score_positive_factors") or [],
        "tags": lead.get("alert_tags") or [],
    }
