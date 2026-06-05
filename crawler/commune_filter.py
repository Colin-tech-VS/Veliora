"""Filtre ville par commune / code INSEE (aligné sur l'API agrégée)."""

from __future__ import annotations

import re

from crawler.extractors import LeadData
from crawler.fr_communes import resolve_commune


def crawl_commune_row(city: str | None, postcode: str | None = None) -> dict | None:
    """Commune cible du crawl (code INSEE, CP, nom)."""
    city = (city or "").strip()
    if not city:
        return None
    return resolve_commune(city, postcode)


def _city_tokens(text: str | None) -> set[str]:
    return {
        t
        for t in re.sub(r"[^a-z0-9]+", " ", (text or "").lower()).split()
        if len(t) >= 3
    }


def _fuzzy_city_match(lead: LeadData, target_city: str) -> bool:
    """Repli textuel quand le code postal / INSEE manque sur la fiche."""
    from crm.dvf import extract_listing_location

    target_tokens = _city_tokens(target_city)
    if not target_tokens:
        return True
    loc = extract_listing_location(
        getattr(lead, "address", None),
        (getattr(lead, "raw_extras", None) or {}).get("listing_title"),
        getattr(lead, "city", None),
    )
    hay = " ".join(
        p
        for p in (
            loc.get("city"),
            getattr(lead, "address", None),
            getattr(lead, "city", None),
            getattr(lead, "sector", None),
            (getattr(lead, "raw_extras", None) or {}).get("listing_title"),
        )
        if p
    )
    lead_tokens = _city_tokens(hay)
    return bool(target_tokens & lead_tokens)


def lead_matches_commune(
    lead: LeadData,
    target_city: str,
    *,
    target_postcode: str | None = None,
    commune_row: dict | None = None,
) -> bool:
    """True si la fiche appartient à la commune cible (INSEE / CP prioritaire)."""
    target_city = (target_city or "").strip()
    if not target_city:
        return True

    row = commune_row or crawl_commune_row(target_city, target_postcode)
    if not row:
        return _fuzzy_city_match(lead, target_city)

    target_code = (row.get("code") or "").strip()
    target_pc = (row.get("postcode") or target_postcode or "").strip()
    lead_pc = (getattr(lead, "postcode", None) or "").strip()
    lead_city = (getattr(lead, "city", None) or "").strip()

    if lead_pc and target_code:
        lead_row = resolve_commune(lead_city or row.get("name") or "", lead_pc)
        if lead_row and lead_row.get("code") == target_code:
            return True
        if target_pc and lead_pc == target_pc:
            return True
        # CP connu mais commune différente → hors zone
        if lead_row and lead_row.get("code") and lead_row.get("code") != target_code:
            return False
        if target_pc and len(lead_pc) == 5 and lead_pc != target_pc:
            # Même agglo : accepter si le nom de ville correspond
            if not _fuzzy_city_match(lead, target_city):
                return False
            return True

    return _fuzzy_city_match(lead, target_city)
