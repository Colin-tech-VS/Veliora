"""Prospects partagés entre toutes les agences — visibilité par territoire (villes)."""

from __future__ import annotations

from crm.radar import _lead_matches_cities


def is_shared_pool_agency_id(agency_id: str | None) -> bool:
    return agency_id is None or str(agency_id).strip() in ("", "__shared__")


def pool_agency_id() -> None:
    """Valeur `agency_id` en base pour le pool national."""
    return None


def territory_cities_for_agency(agency_id: str) -> list[str]:
    from crawler.storage import get_agency_settings

    raw = get_agency_settings(agency_id).get("target_cities") or []
    return [str(c).strip() for c in raw if c and str(c).strip()]


def lead_visible_to_agency(lead: dict, agency_id: str) -> bool:
    """Fiche visible si pool partagé + ville territoire, ou fiche legacy de l'agence."""
    if not lead:
        return False
    lid = lead.get("agency_id")
    if lid and str(lid).strip() and str(lid) != str(agency_id):
        return False
    if not is_shared_pool_agency_id(lid):
        return True
    cities = territory_cities_for_agency(agency_id)
    if not cities:
        return True
    return _lead_matches_cities(lead, cities)


def filter_leads_for_agency(leads: list[dict], agency_id: str) -> list[dict]:
    return [l for l in leads if lead_visible_to_agency(l, agency_id)]


def shared_leads_sql_where(alias: str = "") -> str:
    """Fragment SQL : lignes du pool partagé (agency_id vide / NULL)."""
    col = f"{alias}agency_id" if alias else "agency_id"
    return f"({col} IS NULL OR TRIM(COALESCE({col}, '')) = '')"
