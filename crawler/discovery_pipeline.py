"""Pipeline unifié d'extraction des URLs d'annonces (tous types de crawl)."""

from __future__ import annotations

from crawler.adapters import BaseAdapter
from crawler.config import MAX_LISTING_LINKS
from crawler.listing_guard import filter_listing_urls


def extract_listing_urls_from_page(
    adapter: BaseAdapter,
    html: str,
    page_url: str,
    *,
    limit: int | None = None,
    use_ai: bool = True,
    ai_attempt: bool = True,
) -> list[str]:
    """Heuristiques portail → adaptatif → générique → IA (optionnel)."""
    if not html or not html.strip():
        return []

    cap = limit or MAX_LISTING_LINKS
    base = adapter.config.base_url or page_url
    batch = filter_listing_urls(
        adapter.find_listings(html, page_url, limit=cap)
    )

    if len(batch) < max(2, cap // 30):
        from crawler.site_discovery import find_listing_links_adaptive

        adaptive = find_listing_links_adaptive(
            html,
            page_url,
            base,
            adapter.config.listing_patterns,
            limit=cap,
        )
        batch = filter_listing_urls(list(dict.fromkeys(batch + adaptive))[:cap])

    if len(batch) < 2:
        from crawler.extractors import find_listing_links
        from crawler.adapters import GenericAdapter

        generic = find_listing_links(
            html,
            page_url,
            GenericAdapter().config.listing_patterns,
            limit=cap,
        )
        batch = filter_listing_urls(list(dict.fromkeys(batch + generic))[:cap])

    if use_ai and ai_attempt and len(batch) < max(3, cap // 20):
        from crawler.ai_discovery import ai_discovery_enabled, ai_extract_listing_urls

        if ai_discovery_enabled():
            ai_links = ai_extract_listing_urls(html, page_url, base, limit=min(60, cap))
            batch = filter_listing_urls(list(dict.fromkeys(batch + ai_links))[:cap])

    return batch
