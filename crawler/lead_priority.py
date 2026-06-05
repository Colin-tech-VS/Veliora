"""Tri des fiches par priorité mandat (partagé HTML + API)."""

from __future__ import annotations

from datetime import datetime, timezone

from crawler.extractors import LeadData


def lead_updated_sort_key(lead: LeadData) -> float:
    """Timestamp négatif — les fiches les plus récentes en premier."""
    raw = (lead.raw_extras or {}).get("streamestate_updated_at") or lead.published_at or ""
    if not raw:
        return 0.0
    try:
        dt = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
        return -dt.timestamp()
    except ValueError:
        return 0.0


def lead_importance_key(lead: LeadData) -> tuple:
    """Particuliers avec contacts et prix/m² plausibles en premier."""
    from crawler.validation import _price_per_m2_plausible

    has_phone = bool(lead.phone and lead.phone != "—")
    has_email = bool(lead.email and lead.email != "—")
    ratio_ok = 0 if _price_per_m2_plausible(lead) else 1
    return (
        0 if lead.type == "particulier" else 1,
        0 if has_phone or has_email else 1,
        ratio_ok,
        0 if lead.surface else 1,
        0 if lead.price else 1,
        lead_updated_sort_key(lead),
    )
