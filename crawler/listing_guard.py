"""Validation des URLs et cohérence des fiches annonce avant enregistrement."""

from __future__ import annotations

import re
from urllib.parse import parse_qs, urlparse

from bs4 import BeautifulSoup

from crawler.extractors import LeadData, is_excluded_listing_url

from crawler.hub_detection import (
    DCF_LISTING_DETAIL_RE,
    FIGARO_HUB_RE,
    FIGARO_LISTING_RE,
    HUB_ADDRESS_RE,
    is_hub_listing_address,
    is_hub_page_title,
    is_multi_listing_html_page,
    is_site_navigation_name,
    is_taxonomy_or_list_hub_url,
)
from crawler.listing_facts import validate_listing_facts_strict

GENERIC_OWNER_RE = re.compile(
    r"^(appartement|maison|studio|terrain|bien|location|vente|achat|parking|immeuble|"
    r"propri[eé]t[eé]|annonce|annonceur|sans\s+agence)$",
    re.IGNORECASE,
)
CATEGORY_PATH_RE = re.compile(
    r"/(?:c/|categorie|category|recherche|search|liste|list\.htm|resultats)",
    re.IGNORECASE,
)


def validate_listing_url(url: str) -> tuple[bool, str]:
    """Vérifie qu'une URL ressemble à une fiche annonce (pas hub / catégorie)."""
    if not url or not url.startswith("http"):
        return False, "URL invalide"
    if is_excluded_listing_url(url):
        return False, "URL exclue (hub, service ou recherche)"

    parsed = urlparse(url.split("#")[0])
    host = parsed.netloc.lower().replace("www.", "")
    path = parsed.path.lower()

    # Page catégorie / recherche — sauf si l'URL porte un identifiant d'annonce fort
    # (ex. /search/visitonline_a_2000028918038 = fiche, pas une liste).
    if CATEGORY_PATH_RE.search(path) and not re.search(r"\d{6,}", path):
        return False, "page catégorie / recherche"

    if "leboncoin.fr" in host:
        if re.search(r"/ad/(?:ventes|locations)(?:_[^/\"'\s]+)?/\d{5,}", url, re.I):
            return True, ""
        if re.search(r"leboncoin\.fr/\d+\.htm", url, re.I):
            return True, ""
        return False, "URL LeBonCoin sans identifiant annonce"

    if "pap.fr" in host:
        if re.search(r"/annonce/(?:vente|location)-[^/\"'\s]+-\d{4,}", url, re.I):
            return True, ""
        if re.search(r"pap\.fr/annonces/\d", url, re.I):
            return True, ""
        return False, "URL PAP sans référence annonce"

    if "seloger.com" in host:
        if re.search(r"/annonces/(?:achat|location|vente|detail)/", path, re.I):
            if re.search(r"\d{5,}", url):
                return True, ""
        if "/detail/" in path and re.search(r"\d{4,}", url):
            return True, ""
        return False, "URL SeLoger invalide"

    if "logic-immo.com" in host or "logicimmo" in host:
        if re.search(r"/(?:vente|location)-[^/]+-\d{4,}", path, re.I):
            return True, ""
        return False, "URL Logic-Immo invalide"

    if "bienici.com" in host:
        if re.search(r"/annonce/\d", path, re.I):
            return True, ""
        if re.search(r"/(?:achat|location)/[^/]+/\d", path, re.I):
            return True, ""
        return False, "URL Bien'ici invalide"

    if "paruvendu.fr" in host:
        if re.search(r"/(?:immobilier|annonces)/[^/]+-\d{4,}", path, re.I):
            return True, ""
        return False, "URL ParuVendu invalide"

    if "bellesdemeures.com" in host:
        if is_taxonomy_or_list_hub_url(url):
            return False, "page liste Belles Demeures (pas une fiche)"
        if DCF_LISTING_DETAIL_RE.search(url):
            return True, ""
        if re.search(r"\d{7,}", path):
            return True, ""
        return False, "URL Belles Demeures sans identifiant de fiche"

    if "lefigaro.fr" in host or "figaro" in host:
        if re.search(r"france\.html", path, re.I):
            return False, "index national Figaro"
        if FIGARO_LISTING_RE.search(url):
            return True, ""
        if re.search(r"/annonces/[^/\"'\s]*\d{5,}", url, re.I):
            return True, ""
        if FIGARO_HUB_RE.search(path) and not re.search(r"\d{5,}", path):
            return False, "page catégorie / ville Figaro"
        if re.search(
            r"immobilier-(vente|location)-(?:appartement|maison|bien|studio)-[a-z0-9+.+-]+\.html",
            url,
            re.I,
        ) and not re.search(r"\d{5,}", path):
            return False, "page ville Figaro (pas une annonce)"
        if re.search(r"\d{5,}", path) and "/annonces/" in path:
            return True, ""
        return False, "URL Figaro sans identifiant annonce"

    if re.search(r"/(?:annonce|detail|listing|property|ad)/[^?]*\d{4,}", url, re.I):
        return True, ""
    if re.search(r"/\d{6,}(?:[/?]|$)", path):
        return True, ""
    if re.search(r"[\-_/]\d{5,}\.html?", url, re.I):
        return True, ""
    # Identifiant numérique fort (≥6 chiffres) en fin de slug — fiche détail générique
    # (ex. /search/visitonline_a_2000028918038, /bien_123456).
    if re.search(r"[/_\-]\d{6,}", path):
        return True, ""

    if "staticlbi.com" in host:
        if re.search(r"\d{4,}", path) or len(path) > 12:
            return True, ""

    return False, "URL sans identifiant d'annonce"


LISTING_PATH_HINTS_RE = re.compile(
    r"(?:annonce|detail|listing|property|bien|offre|achat|location|vente|"
    r"louer|acheter|immobilier|real-estate|house|flat|ad/|fiche)",
    re.IGNORECASE,
)


def validate_listing_url_import(url: str) -> tuple[bool, str]:
    """Validation souple pour import manuel d'une fiche (tous sites)."""
    if not url or not url.startswith("http"):
        return False, "URL invalide"
    if is_excluded_listing_url(url):
        return False, "URL exclue (page liste ou service)"

    ok_strict, _ = validate_listing_url(url)
    if ok_strict:
        return True, ""

    parsed = urlparse(url.split("#")[0])
    path = parsed.path.lower()
    if CATEGORY_PATH_RE.search(path) and not re.search(r"\d{4,}", path):
        return False, "page recherche — collez le lien direct de la fiche"

    if len(path) < 2 or path in ("/", ""):
        return False, "URL trop courte"

    if re.search(
        r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}",
        url,
        re.I,
    ):
        return True, ""

    if LISTING_PATH_HINTS_RE.search(path):
        return True, ""

    if re.search(r"\d{4,}", path):
        return True, ""

    qs = parse_qs(parsed.query)
    for key in ("id", "annonce", "ref", "listing", "ad", "property", "bien"):
        vals = qs.get(key) or []
        if vals and re.search(r"\d+", str(vals[0])):
            return True, ""

    segments = [s for s in path.split("/") if s]
    if len(segments) >= 2 and any(len(s) > 8 for s in segments):
        return True, ""

    return False, "Collez le lien direct de la fiche annonce (pas une page de résultats)"


def _reject_list_or_mixed_listing_page(
    url: str,
    html: str | None,
    lead: LeadData,
) -> tuple[bool, str] | None:
    """Rejette pages liste / mix annonces — None si la page peut être une fiche."""
    if is_taxonomy_or_list_hub_url(url):
        return False, "page liste (URL catégorie)"
    if html and is_multi_listing_html_page(html, url):
        return False, "page liste — plusieurs annonces"
    addr = (lead.address or "").strip()
    if addr and is_hub_listing_address(addr):
        return False, "page liste, pas une fiche détail"
    if html:
        title_m = re.search(r"<title[^>]*>([^<]{5,300})</title>", html, re.I)
        if title_m and is_hub_page_title(title_m.group(1)):
            return False, "titre page = liste d'annonces"
    return None


def validate_listing_coherence_import(
    url: str,
    html: str | None,
    lead: LeadData,
) -> tuple[bool, str]:
    """Cohérence assouplie : enregistrer dès qu'une fiche est identifiable."""
    rejected = _reject_list_or_mixed_listing_page(url, html, lead)
    if rejected:
        return rejected

    ok_url, url_reason = validate_listing_url_import(url)
    if not ok_url:
        return False, url_reason

    snippet = (html or "")[:8000].lower()
    if snippet and "annonces similaires" in snippet and len(snippet) < 2500:
        if not lead.phone and not lead.email and not lead.price:
            return False, "page liste sans fiche détail"

    from crawler.errors import format_missing_fields
    from crawler.validation import missing_core_fields

    core_miss = missing_core_fields(lead)
    if not core_miss:
        return True, ""

    addr = (lead.address or "").strip()
    has_partial = bool(
        (lead.phone and lead.phone != "—")
        or (lead.email and lead.email != "—")
        or (addr and len(addr) >= 5)
        or lead.surface
        or lead.price
        or (lead.price and lead.surface)
    )
    if has_partial and len(core_miss) <= 2:
        return True, ""

    return False, (
        f"champs requis manquants : {format_missing_fields(core_miss)} "
        "(adresse, téléphone, email, m²)"
    )


def validate_field_coherence(lead: LeadData) -> tuple[bool, str]:
    """Détecte un mix prix/surface évident (seuils larges pour ne pas tout rejeter)."""
    if not lead.price or not lead.surface or lead.surface <= 0:
        return True, ""

    tx = lead.transaction_type or "vente"
    ratio = lead.price / lead.surface
    if tx == "location":
        if ratio < 1 or ratio > 800:
            return False, f"loyer/surface incohérent ({ratio:.0f} €/m²)"
        if lead.price > 120_000:
            return False, "loyer trop élevé (confusion avec prix de vente?)"
    else:
        if ratio < 80 or ratio > 120_000:
            return False, f"prix/surface incohérent ({ratio:.0f} €/m²)"
        from crawler.config import PRICE_MIN_SALE_EUR

        if lead.price < max(500, PRICE_MIN_SALE_EUR // 4):
            return False, "prix vente trop bas (confusion avec loyer?)"

    return True, ""


def should_withdraw_incoherent(reason: str) -> bool:
    """
    Retrait CRM uniquement pour incohérences graves (hub, mix annonces).
    Pas pour « fiche pauvre », téléphone footer, etc.
    """
    r = (reason or "").lower()
    withdraw_markers = (
        "page liste",
        "hub",
        "titre de page liste",
        "titre page = hub",
        "plusieurs annonces",
        "plusieurs prix/surfaces",
        "mix annonces",
        "contradictoire",
        "page catégorie",
        "index national",
        "sans identifiant",
        "url exclue",
        "page recherche",
    )
    if any(m in r for m in withdraw_markers):
        return True
    skip_markers = (
        "fiche trop pauvre",
        "peu de données",
        "téléphone hors",
        "mix contacts",
        "nom = menu",
        "nom = type",
        "incohérent",
        "loyer/surface",
        "prix/surface",
    )
    if any(m in r for m in skip_markers):
        return False
    return False


def validate_phone_in_listing(lead: LeadData, soup: BeautifulSoup | None, page_url: str) -> tuple[bool, str]:
    """Le téléphone doit provenir du bloc annonce, pas du footer ou des similaires."""
    if not lead.phone or lead.phone == "—" or soup is None:
        return True, ""

    from crawler.extractors import _get_hero_block, get_main_content_root, normalize_phone, is_in_excluded_zone

    main = get_main_content_root(soup, page_url)
    hero = _get_hero_block(main)
    digits = re.sub(r"\D", "", lead.phone)
    for tel in hero.select('a[href^="tel:"]'):
        if is_in_excluded_zone(tel):
            continue
        href_digits = re.sub(r"\D", "", tel.get("href", "").replace("tel:", ""))
        if href_digits.endswith(digits[-9:]) or digits.endswith(href_digits[-9:]):
            return True, ""
    hero_text = hero.get_text(" ", strip=True)
    if digits[-9:] in re.sub(r"\D", "", hero_text):
        return True, ""
    return False, "téléphone hors bloc annonce (mix contacts?)"


def validate_listing_coherence(
    url: str,
    html: str | None,
    lead: LeadData,
) -> tuple[bool, str]:
    """Contrôle strict (retrait / audit) — hub et mix annonces évidents."""
    addr = (lead.address or "").strip()
    if addr and is_hub_listing_address(addr):
        return False, "titre de page liste, pas une adresse"

    fn = (lead.first_name or "").strip()
    ln = (lead.last_name or "").strip()
    if fn and GENERIC_OWNER_RE.match(fn):
        if not ln or GENERIC_OWNER_RE.match(ln) or ln.lower() == fn.lower():
            return False, "nom = type de bien (extraction page hub)"
    if is_site_navigation_name(fn) or is_site_navigation_name(ln):
        return False, "nom = menu du site (pas un contact)"
    if is_site_navigation_name(f"{fn} {ln}".strip()):
        return False, "nom = menu du site (pas un contact)"

    ok_url, url_reason = validate_listing_url(url)
    if not ok_url:
        ok_url, url_reason = validate_listing_url_import(url)
    if not ok_url:
        return False, url_reason

    snippet = (html or "")[:12000].lower()
    if snippet:
        if "annonces similaires" in snippet and len(snippet) < 2000 and not lead.phone and not lead.price:
            return False, "page liste sans fiche détail"
        title_m = re.search(r"<title[^>]*>([^<]{5,200})</title>", html or "", re.I)
        if title_m:
            title = title_m.group(1)
            if HUB_ADDRESS_RE.search(title) and not lead.phone and not lead.price:
                return False, "titre page = hub annonces"

    ok_facts, facts_reason = validate_listing_facts_strict(
        lead, BeautifulSoup(html, "lxml") if html else None, url
    )
    if not ok_facts and should_withdraw_incoherent(facts_reason):
        return False, facts_reason

    return True, ""


def validate_listing_coherence_crawl(
    url: str,
    html: str | None,
    lead: LeadData,
) -> tuple[bool, str]:
    """Cohérence crawl : rejette listes/mix, puis valide champs et ratios."""
    rejected = _reject_list_or_mixed_listing_page(url, html, lead)
    if rejected:
        return rejected

    ok_field, field_reason = validate_field_coherence(lead)
    if not ok_field:
        return False, field_reason

    soup = BeautifulSoup(html, "lxml") if html else None
    ok_facts, facts_reason = validate_listing_facts_strict(lead, soup, url)
    if not ok_facts and should_withdraw_incoherent(facts_reason):
        return False, facts_reason

    return validate_listing_coherence_import(url, html, lead)


def filter_listing_urls(urls: list[str]) -> list[str]:
    """URLs fiches annonce — validation souple d'abord (ne pas vider la liste)."""
    out: list[str] = []
    seen: set[str] = set()
    for u in urls:
        u = u.split("#")[0].strip()
        if not u or u in seen:
            continue
        ok, _ = validate_listing_url_import(u)
        if not ok:
            ok, _ = validate_listing_url(u)
        if ok:
            seen.add(u)
            out.append(u)
    return out
