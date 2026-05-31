"""Découverte adaptative des annonces sur un site entier (multi-seeds, BFS, heuristiques)."""

from __future__ import annotations

import re
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup

from crawler.extractors import find_listing_links, is_excluded_listing_url
from crawler.listing_guard import validate_listing_url
from crawler.url_utils import registrable_domain

IMMO_PATH_HINTS = re.compile(
    r"annonce|immobilier|vente|location|achat|louer|louer|bien|appartement|"
    r"maison|studio|terrain|parking|immo|property|listing|real.?estate",
    re.IGNORECASE,
)

HUB_PATH_RE = re.compile(
    r"/(?:recherche|search|login|register|contact|aide|blog|presse|legal|"
    r"cookie|account|compte|cart|panier|newsletter|faq|about|qui-sommes)",
    re.IGNORECASE,
)

CATEGORY_PATH_RE = re.compile(
    r"/(?:annonces?|catalogue|listings?|vente|location|achat|louer|"
    r"immobilier|biens?|properties)(?:/|$|[?#])",
    re.IGNORECASE,
)

LISTING_PATH_RE = re.compile(
    r"/(?:annonce|detail|listing|property|ad|bien|fiche|ref)[^/]*[/\-_]\d{4,}|"
    r"/(?:annonce|detail|listing|property|ad)/[^/?#]+\d{4,}|"
    r"[\-_/]\d{6,}(?:[/?]|$|\.html?)|"
    r"/\d{6,}\.html?",
    re.IGNORECASE,
)

COMMON_IMMO_PATHS = (
    "/annonces",
    "/annonce",
    "/acheter",
    "/acheter-maison",
    "/acheter-appartement",
    "/louer-maison",
    "/louer-appartement",
    "/vendre",
    "/a-vendre",
    "/a-louer",
    "/recherche",
    "/search",
    "/vente",
    "/vente-maison",
    "/vente-appartement",
    "/location",
    "/achat",
    "/louer",
    "/immobilier",
    "/listings",
    "/properties",
    "/catalogue",
    "/biens",
)

DEFAULT_GENERIC_PATTERNS = [
    r"staticlbi\.com/[^\"'\s]+",
    r"[a-z0-9-]+\.staticlbi\.com/[^\"'\s]+",
    r"/annonce[^/\"'\s]*/\d{4,}",
    r"/annonces/[^/\"'\s]+-\d{4,}",
    r"/fiche[^/\"'\s]*/\d{4,}",
    r"/detail[^/\"'\s]*/\d{4,}",
    r"/listing[^/\"'\s]*/\d{4,}",
    r"/property[^/\"'\s]*/\d{4,}",
    r"/ad/\d{4,}",
    r"/bien[^/\"'\s]*/\d{4,}",
    r"/ref[_-]?\d{5,}",
    r"[\-_/]\d{6,}\.html?",
    r"/\d{6,}(?:[/?]|$)",
]


def _same_site(url: str, base_url: str) -> bool:
    if not url or not base_url:
        return False
    try:
        a = registrable_domain(urlparse(url).netloc)
        b = registrable_domain(urlparse(base_url).netloc)
        return bool(a and b and a == b)
    except Exception:
        return False


def build_site_seed_urls(base_url: str, search_url: str) -> list[str]:
    """Points d'entrée pour explorer tout le site (pas une seule page)."""
    seeds: list[str] = []
    for raw in (search_url, base_url):
        u = (raw or "").strip().rstrip("/")
        if u and u.startswith("http") and u not in seeds:
            seeds.append(u)

    if not base_url:
        return seeds[:26]

    parsed = urlparse(base_url)
    origin = f"{parsed.scheme}://{parsed.netloc}"

    for path in COMMON_IMMO_PATHS:
        candidate = f"{origin}{path}"
        if candidate not in seeds:
            seeds.append(candidate)

    # Chemins dérivés du search_url (ex. /vente-appartement → /vente-maison)
    if search_url:
        sp = urlparse(search_url)
        parts = [p for p in sp.path.split("/") if p]
        if parts:
            parent = f"{sp.scheme}://{sp.netloc}/{'/'.join(parts[:1])}"
            if parent not in seeds:
                seeds.append(parent.rstrip("/"))

    return seeds[:26]


PORTAL_DISCOVER_URLS: dict[str, list[str]] = {
    "leboncoin": [
        "https://www.leboncoin.fr/recherche?category=9&real_estate_type=2",
        "https://www.leboncoin.fr/recherche?category=9&real_estate_type=1",
        "https://www.leboncoin.fr/recherche?category=10",
    ],
    "pap": [
        "https://www.pap.fr/annonce/vente-appartements",
        "https://www.pap.fr/annonce/vente-maisons",
        "https://www.pap.fr/annonce/location-appartements",
        "https://www.pap.fr/annonce/location-maisons",
    ],
    "seloger": [
        "https://www.seloger.com/list.htm?types=1&projects=2",
        "https://www.seloger.com/list.htm?types=2&projects=1",
    ],
    "logicimmo": [
        "https://www.logic-immo.com/vente-appartement",
        "https://www.logic-immo.com/vente-maison",
        "https://www.logic-immo.com/location-appartement",
    ],
    "bienici": [
        "https://www.bienici.com/recherche/achat/appartement",
        "https://www.bienici.com/recherche/achat/maison",
        "https://www.bienici.com/recherche/location/appartement",
    ],
    "paruvendu": [
        "https://www.paruvendu.fr/immobilier/vente/",
        "https://www.paruvendu.fr/immobilier/location/",
    ],
    "lefigaro": [
        "https://immobilier.lefigaro.fr/annonces/immobilier-vente-appartement.html",
        "https://immobilier.lefigaro.fr/annonces/immobilier-vente-maison.html",
        "https://immobilier.lefigaro.fr/annonces/immobilier-location-appartement.html",
        "https://immobilier.lefigaro.fr/annonces/immobilier-location-maison.html",
    ],
}


def get_portal_discover_urls(
    source_id: str | None,
    adapter,
    primary_search_url: str = "",
) -> list[str]:
    """URLs de départ pour explorer tout un portail (vente + location + types de biens)."""
    from crawler.portals import resolve_base_portal_id

    urls: list[str] = []
    base_id = resolve_base_portal_id(source_id or "")
    if base_id and base_id in PORTAL_DISCOVER_URLS:
        urls.extend(PORTAL_DISCOVER_URLS[base_id])

    for raw in (
        primary_search_url,
        getattr(adapter.config, "search_url", "") if adapter else "",
        getattr(adapter.config, "base_url", "") if adapter else "",
    ):
        u = (raw or "").strip().rstrip("/")
        if u.startswith("http") and u not in urls:
            urls.append(u)

    if adapter and adapter.config.base_url:
        urls.extend(build_site_seed_urls(adapter.config.base_url, primary_search_url or urls[0] if urls else ""))
        from crawler.host_discovery import discover_urls_for_host

        urls.extend(
            discover_urls_for_host(
                adapter.config.base_url,
                primary_search_url or getattr(adapter.config, "search_url", ""),
            )
        )

    out: list[str] = []
    for u in urls:
        u = u.split("#")[0].rstrip("/")
        if u and u not in out:
            out.append(u)
    return out[:24]


def score_listing_link(url: str, anchor_text: str = "") -> int:
    """Score heuristique : URL ressemble à une fiche annonce."""
    if not url or is_excluded_listing_url(url):
        return -100
    ok, _ = validate_listing_url(url)
    if ok:
        return 80

    path = urlparse(url).path.lower()
    score = 0
    text = (anchor_text or "").lower()

    if LISTING_PATH_RE.search(url):
        score += 45
    if re.search(r"\d{5,}", url):
        score += 25
    if re.search(r"(?:annonce|detail|listing|property|/ad/|/bien/)", path, re.I):
        score += 20
    if IMMO_PATH_HINTS.search(path) and re.search(r"\d{4,}", url):
        score += 15
    if re.search(r"(?:m²|m2|€|eur|prix|pièces|pieces|chambres)", text, re.I):
        score += 12
    if re.search(r"(?:appartement|maison|studio|villa|terrain|loft)", text, re.I):
        score += 8

    if HUB_PATH_RE.search(path):
        score -= 30
    if re.search(r"/(?:page|p)[/-]?\d+$", path, re.I) and not re.search(r"\d{5,}", url):
        score -= 10
    if path.count("/") <= 2 and not re.search(r"\d{4,}", url):
        score -= 15

    return score


def score_category_link(url: str, anchor_text: str = "") -> int:
    """Score pour pages index / catégories à explorer."""
    if not url or is_excluded_listing_url(url):
        return -100
    if score_listing_link(url, anchor_text) >= 50:
        return -50

    path = urlparse(url).path.lower()
    score = 0
    if CATEGORY_PATH_RE.search(path):
        score += 35
    if IMMO_PATH_HINTS.search(path):
        score += 20
    if re.search(r"(?:vente|location|achat|louer)", path, re.I):
        score += 15
    if re.search(r"(?:appartement|maison|studio|terrain|immobilier)", anchor_text or "", re.I):
        score += 10
    if HUB_PATH_RE.search(path):
        score -= 40
    if re.search(r"\d{6,}", url):
        score -= 20
    return score


def find_listing_links_adaptive(
    html: str,
    page_url: str,
    base_url: str,
    patterns: list[str] | None = None,
    limit: int = 500,
) -> list[str]:
    """Combine patterns portail + heuristiques pour sites inconnus."""
    pats = list(patterns or []) + DEFAULT_GENERIC_PATTERNS
    seen: set[str] = set()
    scored: list[tuple[int, str]] = []

    pattern_links = find_listing_links(html, page_url, pats, limit=limit)
    for link in pattern_links:
        if link not in seen:
            seen.add(link)
            scored.append((70, link))

    soup = BeautifulSoup(html, "lxml")
    for a in soup.find_all("a", href=True):
        href = a.get("href", "")
        full = urljoin(page_url, href).split("#")[0]
        if full in seen or not full.startswith("http"):
            continue
        from crawler.host_discovery import is_related_listing_host

        if not is_related_listing_host(full, base_url or "", page_url):
            continue
        if is_excluded_listing_url(full):
            continue
        text = a.get_text(" ", strip=True)
        s = score_listing_link(full, text)
        if s >= 28:
            seen.add(full)
            scored.append((s, full))

    scored.sort(key=lambda x: -x[0])
    return [u for _, u in scored[:limit]]


def find_category_links(
    html: str,
    page_url: str,
    base_url: str,
    visited: set[str] | None = None,
    limit: int = 25,
) -> list[str]:
    """Liens internes vers listes / rubriques immo à parcourir."""
    visited = visited or set()
    soup = BeautifulSoup(html, "lxml")
    scored: list[tuple[int, str]] = []
    seen: set[str] = set()

    for a in soup.find_all("a", href=True):
        full = urljoin(page_url, a["href"]).split("#")[0].rstrip("/")
        if full in seen or full in visited:
            continue
        from crawler.host_discovery import is_related_listing_host

        if not full.startswith("http"):
            continue
        if not is_related_listing_host(full, base_url or "", page_url):
            continue
        if is_excluded_listing_url(full):
            continue
        text = a.get_text(" ", strip=True)
        s = score_category_link(full, text)
        if s >= 20:
            seen.add(full)
            scored.append((s, full))

    scored.sort(key=lambda x: -x[0])
    return [u for _, u in scored[:limit]]


def infer_patterns_from_urls(urls: list[str], max_patterns: int = 8) -> list[str]:
    """Apprend des motifs d'URL récurrents sur le site crawlé."""
    if len(urls) < 3:
        return []

    samples = urls[:40]
    host_fragments: dict[str, int] = {}

    for url in samples:
        parsed = urlparse(url)
        host = re.escape(parsed.netloc.replace("www.", ""))
        path = parsed.path
        seg = re.sub(r"\d{4,}", r"\\d{4,}", path)
        seg = re.sub(r"[a-f0-9]{8,}", r"[a-f0-9]+", seg, flags=re.I)
        if len(seg) < 6:
            continue
        pat = rf"{host}{re.escape(seg) if seg == path else seg}"
        if "\\d" in pat or "annonce" in pat.lower() or "detail" in pat.lower():
            host_fragments[pat] = host_fragments.get(pat, 0) + 1

    ranked = sorted(host_fragments.items(), key=lambda x: -x[1])
    return [p for p, c in ranked[:max_patterns] if c >= 2]


def extend_adapter_patterns(adapter, new_urls: list[str]) -> None:
    """Enrichit les patterns de l'adaptateur au fil du crawl."""
    inferred = infer_patterns_from_urls(new_urls)
    if not inferred:
        return
    existing = list(getattr(adapter.config, "listing_patterns", []) or [])
    merged = existing + inferred + DEFAULT_GENERIC_PATTERNS
    adapter.config.listing_patterns = list(dict.fromkeys(merged))[:40]
