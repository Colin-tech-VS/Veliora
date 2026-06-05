"""Découverte d'annonces par hôte (sites agence, LBI / staticlbi, etc.)."""

from __future__ import annotations

from urllib.parse import urlparse

# Hôtes CDN / moteur d'annonces fréquents sur les sites agence
LISTING_CDN_HOST_MARKERS = (
    "staticlbi.com",
    "netty.fr",
    "hektor.fr",
    "hektor.io",
    "apimo.net",
    "whise.com",
    "modelo.office",
)

# Thèmes WordPress immo courants (RealHomes, Houzez, WpResidence…)
WORDPRESS_IMMO_PATTERNS = [
    r"/property/[^/\"'\s]+",
    r"/properties/[^/\"'\s]+",
    r"/listing/[^/\"'\s]+",
    r"/listings/[^/\"'\s]+",
    r"/annonce[s]?/[^/\"'\s]+",
    r"/bien[s]?/[^/\"'\s]+",
    r"/offre[s]?/[^/\"'\s]+",
    r"\?post_type=(?:property|immobilier|annonce|real_estate)",
]

# Moteurs CRM / flux XML (Apimo, Hektor, Netty, Whise…) sur sites agence
CRM_ENGINE_PATTERNS = [
    r"[a-z0-9-]+\.netty\.fr/[^\"'\s]*\d{4,}",
    r"apimo\.net/[^\"'\s]*\d{4,}",
    r"whise\.com/[^\"'\s]*\d{4,}",
    r"/ref[_-]?\d{5,}",
    r"/fiche[_-]?\d{5,}",
]

IMMObilIER_FRANCE_PATTERNS = [
    r"staticlbi\.com/[^\"'\s]+",
    r"[a-z0-9-]+\.staticlbi\.com/[^\"'\s]+",
    r"immobilier-france\.fr/[^\"'\s]*\d{4,}",
    r"immobilier-france\.fr/(?:annonce|fiche|bien|detail|property)[^\"'\s]*",
    r"/annonce[s]?/[^/\"'\s]+-\d{4,}",
    r"/fiche[s]?/[^/\"'\s]*\d{4,}",
    r"/ref-\d{4,}",
    r"/\d{5,}(?:[/?]|$|\.html?)",
]


def host_needs_browser(url: str) -> bool:
    """Sites agence souvent en JS / LBI."""
    host = urlparse(url).netloc.lower()
    if any(m in host for m in LISTING_CDN_HOST_MARKERS):
        return True
    if "immobilier-france" in host:
        return True
    return False


def is_listing_cdn_url(url: str) -> bool:
    host = urlparse(url).netloc.lower()
    return any(m in host for m in LISTING_CDN_HOST_MARKERS)


def is_related_listing_host(url: str, base_url: str, page_url: str = "") -> bool:
    """Même site ou CDN d'annonces lié (ex. staticlbi pour immobilier-france.fr)."""
    if not url:
        return False
    try:
        from crawler.site_discovery import _same_site

        if _same_site(url, base_url or page_url):
            return True
    except Exception:
        pass
    if is_listing_cdn_url(url):
        return True
    return False


def extra_patterns_for_host(base_url: str, search_url: str = "") -> list[str]:
    host = urlparse(base_url or search_url).netloc.lower()
    patterns: list[str] = []
    if "immobilier-france" in host:
        patterns.extend(IMMObilIER_FRANCE_PATTERNS)
    patterns.extend(
        [
            r"staticlbi\.com/[^\"'\s]+",
            r"/fiche[^/\"'\s]*\d{4,}",
            r"/ref[_-]?\d{5,}",
        ]
    )
    if ".netty.fr" in host or "apimo" in host or "whise" in host or "hektor" in host:
        patterns.extend(CRM_ENGINE_PATTERNS)
    if any(
        x in host
        for x in ("wordpress", "wp-content", "immobilier", "agence", "immo")
    ):
        patterns.extend(WORDPRESS_IMMO_PATTERNS)
    patterns.extend(WORDPRESS_IMMO_PATTERNS[:6])
    patterns.extend(CRM_ENGINE_PATTERNS)
    return list(dict.fromkeys(patterns))


def discover_urls_for_host(base_url: str, search_url: str = "") -> list[str]:
    """Pages d'entrée utiles pour sites agence / immobilier-france."""
    seeds: list[str] = []
    for raw in (search_url, base_url):
        u = (raw or "").strip().rstrip("/")
        if u.startswith("http") and u not in seeds:
            seeds.append(u)
    if not base_url:
        return seeds

    parsed = urlparse(base_url)
    origin = f"{parsed.scheme}://{parsed.netloc}"
    host = parsed.netloc.lower()

    extra_paths = list(
        {
            "/annonces",
            "/annonce",
            "/vente",
            "/location",
            "/achat",
            "/louer",
            "/recherche",
            "/catalogue",
            "/biens",
            "/immobilier",
            "/liste",
            "/offres",
        }
    )
    if "immobilier-france" in host:
        extra_paths.extend(
            [
                "/vente-maison",
                "/vente-appartement",
                "/location-maison",
                "/location-appartement",
                "/annonces/vente",
                "/annonces/location",
            ]
        )

    for path in extra_paths:
        candidate = f"{origin}{path}"
        if candidate not in seeds:
            seeds.append(candidate)
    return seeds[:20]
