"""Point d'entrée — enrichissement lead avec scoring V2."""

from __future__ import annotations

import json
from typing import Any

from crm.scoring.explain import build_score_explanation
from crm.scoring.mandate import compute_mandate_score, days_since
from crm.scoring.price_history import count_price_drops_from_history
from crm.scoring.weights import merge_weights


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


def enrich_lead_scores(lead: dict) -> dict:
    """Calcule Score Mandat™, explication JSON et champs dérivés."""
    lead = _load_scoring_context(dict(lead))
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
    if lead.get("previous_price") and lead.get("price"):
        lead["price_change_pct"] = round(
            (lead["price"] - lead["previous_price"]) / lead["previous_price"] * 100,
            1,
        )
    else:
        lead["price_change_pct"] = None

    return lead


def enrich_lead_row(lead: dict) -> dict:
    """Alias compat radar / storage."""
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
