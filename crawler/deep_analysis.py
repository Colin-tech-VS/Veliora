"""Analyse approfondie — recrawl navigateur + rotation IP Decodo (CRAWL_PROXIES)."""

from __future__ import annotations

import logging
from typing import Any, Iterator

logger = logging.getLogger(__name__)


def deep_analysis_display_name() -> str:
    from crawler.config import STREAMESTATE_DISPLAY_NAME

    return STREAMESTATE_DISPLAY_NAME


def deep_analysis_configured() -> bool:
    """Prêt si navigateur Playwright + proxies résidentiels (Decodo via CRAWL_PROXIES)."""
    from crawler.config import CRAWL_PLAYWRIGHT_ENABLED, CRAWL_PROXIES

    return bool(CRAWL_PLAYWRIGHT_ENABLED and CRAWL_PROXIES)


def deep_analysis_setup_hint() -> str:
    from crawler.config import antibot_setup_hint

    return antibot_setup_hint(deep_analysis_display_name())


class DeepAnalysisError(Exception):
    """Analyse approfondie indisponible ou interrompue."""


class DeepAnalysisNotConfiguredError(DeepAnalysisError):
    """Decodo / Playwright manquant."""


def _blank(value: Any) -> bool:
    s = str(value).strip() if value is not None else ""
    return s == "" or s == "—"


def lead_needs_verification(row: dict[str, Any]) -> bool:
    """Fiche incomplète : au moins un champ clé manquant."""
    has_phone = not _blank(row.get("phone"))
    has_email = not _blank(row.get("email"))
    if not (has_phone or has_email):
        return True
    if _blank(row.get("surface")) or not row.get("surface"):
        return True
    if _blank(row.get("price")) or not row.get("price"):
        return True
    if _blank(row.get("address")):
        return True
    return False


def _lead_in_city(row: dict[str, Any], city: str | None) -> bool:
    target = (city or "").strip()
    if not target:
        return True
    lead_city = (row.get("city") or "").strip()
    if not lead_city:
        return True
    from crawler.commune_filter import _fuzzy_city_match
    from crawler.validation import lead_from_db_row

    try:
        ld = lead_from_db_row(row)
    except Exception:
        ld = row  # type: ignore[assignment]
    return _fuzzy_city_match(ld, target)


def iter_leads_for_deep_analysis(
    agency_id: str,
    *,
    city: str | None = None,
    only_incomplete: bool = True,
    limit: int | None = None,
) -> Iterator[dict[str, Any]]:
    """Prospects éligibles au recrawl approfondi (URL annonce requise)."""
    from crawler.config import DEEP_ANALYSIS_MAX_LEADS_PER_RUN
    from crawler.storage import get_leads

    cap = max(1, int(limit if limit is not None else DEEP_ANALYSIS_MAX_LEADS_PER_RUN))
    n = 0
    for row in get_leads(agency_id, enrich=False):
        if (row.get("status") or "nouveau") == "retire":
            continue
        if not (row.get("source_url") or "").strip():
            continue
        if only_incomplete and not lead_needs_verification(row):
            continue
        if not _lead_in_city(row, city):
            continue
        yield row
        n += 1
        if n >= cap:
            break


def count_deep_analysis_candidates(
    agency_id: str,
    *,
    city: str | None = None,
    only_incomplete: bool = True,
) -> int:
    return sum(
        1
        for _ in iter_leads_for_deep_analysis(
            agency_id,
            city=city,
            only_incomplete=only_incomplete,
            limit=10_000,
        )
    )
