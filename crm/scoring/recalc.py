"""Point d'entrée — enrichissement lead avec scoring V2."""

from __future__ import annotations

import json
from typing import Any

from crm.scoring.explain import build_score_explanation
from crm.scoring.mandate import compute_mandate_score, days_since
from crm.scoring.price_history import count_price_drops_from_history
from crm.scoring.weights import merge_weights


class ScoringBatchContext:
    """Poids agence + historiques prix + clients préchargés (évite N requêtes SQL)."""

    __slots__ = ("weights", "history_by_lead", "clients", "agency_id")

    def __init__(
        self,
        weights: dict[str, float],
        history_by_lead: dict[int, list[dict]],
        clients: list[dict] | None = None,
        agency_id: str | None = None,
    ) -> None:
        self.weights = weights
        self.history_by_lead = history_by_lead
        self.clients = clients or []
        self.agency_id = agency_id


def _load_agency_clients(agency_id: str) -> list[dict]:
    try:
        from crm.mandates.storage import list_property_clients

        return list_property_clients(str(agency_id))
    except Exception:
        return []


def load_scoring_batch_context(agency_id: str, lead_ids: list[int]) -> ScoringBatchContext:
    weights = merge_weights(None)
    history: dict[int, list[dict]] = {}
    clients: list[dict] = []
    if not agency_id or not lead_ids:
        return ScoringBatchContext(weights, history, clients, agency_id)
    try:
        from crawler.storage import get_connection
        from crm.scoring.price_history import fetch_price_history_map
        from crm.scoring.weights import load_agency_weights

        with get_connection() as conn:
            weights = load_agency_weights(conn, str(agency_id))
            history = fetch_price_history_map(conn, str(agency_id), lead_ids)
        clients = _load_agency_clients(agency_id)
    except Exception:
        pass
    return ScoringBatchContext(weights, history, clients, agency_id)


def _inject_demand(lead: dict, clients: list[dict]) -> None:
    if not clients:
        return
    try:
        from crm.matching.service import demand_counts

        lead["demand_matches"] = demand_counts(lead, clients)
    except Exception:
        pass


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
    _inject_demand(lead, ctx.clients)
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
        _inject_demand(lead, _load_agency_clients(agency_id))
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
    lead["signature_probability"] = explanation["signature_probability"]
    lead["signature_band"] = explanation["signature_band"]
    lead["signature_tone"] = explanation["signature_tone"]
    lead["signature_label"] = explanation["signature_label"]
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


def hydrate_lead_from_stored(lead: dict) -> dict:
    """Champs dérivés pour l'API liste — sans recalcul complet (scores déjà en base)."""
    lead = dict(lead)
    expl = lead.get("score_explanation")
    if isinstance(expl, str):
        try:
            expl = json.loads(expl)
            lead["score_explanation"] = expl
        except json.JSONDecodeError:
            expl = None
    if isinstance(expl, dict):
        lead["alert_tags"] = expl.get("tags") or []
        lead["priority_tier"] = expl.get("priority_tier") or lead.get("priority_tier")
        lead["score_positive_factors"] = expl.get("positive_factors") or []
        lead["score_negative_factors"] = expl.get("negative_factors") or []
    else:
        lead.setdefault("alert_tags", [])

    ms = lead.get("mandate_score")
    if ms is not None:
        lead["score"] = ms

    # Probabilité de signature : depuis l'explication stockée si présente,
    # sinon (anciens enregistrements) recalcul direct depuis le score + contact.
    from crm.scoring.probability import signature_probability

    if isinstance(expl, dict) and expl.get("signature_probability") is not None:
        lead["signature_probability"] = expl.get("signature_probability")
        lead["signature_band"] = expl.get("signature_band")
        lead["signature_tone"] = expl.get("signature_tone")
        lead["signature_label"] = expl.get("signature_label")
    else:
        sig = signature_probability(lead, ms if ms is not None else 0)
        lead["signature_probability"] = sig["probability"]
        lead["signature_band"] = sig["band"]
        lead["signature_tone"] = sig["tone"]
        lead["signature_label"] = sig["label"]
    lead["days_on_market"] = days_since(lead.get("published_at") or lead.get("listedAt"))

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


def hydrate_leads_for_list(leads: list[dict], agency_id: str) -> list[dict]:
    """Liste CRM : hydrate depuis la base ; recalcul seulement si score absent."""
    if not leads:
        return leads
    missing = [l for l in leads if l.get("mandate_score") is None and l.get("id")]
    enriched_by_id: dict[int, dict] = {}
    if missing:
        ctx = load_scoring_batch_context(
            agency_id,
            [int(l["id"]) for l in missing],
        )
        for raw in missing:
            lid = int(raw["id"])
            try:
                enriched_by_id[lid] = enrich_lead_scores(dict(raw), scoring_ctx=ctx)
            except Exception:
                enriched_by_id[lid] = hydrate_lead_from_stored(raw)

    out: list[dict] = []
    for raw in leads:
        lid = raw.get("id")
        if lid is not None and int(lid) in enriched_by_id:
            out.append(enriched_by_id[int(lid)])
        elif raw.get("_scores_enriched"):
            out.append(raw)
        else:
            try:
                out.append(hydrate_lead_from_stored(raw))
            except Exception:
                out.append(raw)
    return out


def batch_enrich_leads(leads: list[dict], agency_id: str) -> list[dict]:
    """Recalcul complet — réservé aux traitements explicites (pas GET /leads)."""
    if not leads:
        return leads
    lead_ids = [int(l["id"]) for l in leads if l.get("id")]
    ctx = load_scoring_batch_context(agency_id, lead_ids)
    out: list[dict] = []
    for raw in leads:
        try:
            out.append(enrich_lead_scores(dict(raw), scoring_ctx=ctx))
        except Exception:
            out.append(hydrate_lead_from_stored(raw))
    return out


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
