"""Collecte multi-sources et vérification croisée des faits annonce (titre, m², prix, date)."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import date
from typing import Any, Literal

from bs4 import BeautifulSoup

from crawler.extractors import (
    LeadData,
    _domain_key_from_url,
    _element_order,
    _element_zone_attrs,
    _find_json_date,
    _get_hero_block,
    _get_hero_text,
    _is_false_price_element,
    _parse_date_string,
    _parse_surface_value,
    _pick_listing_element,
    _price_match_is_per_m2,
    _strip_combined_price_per_m2,
    _transaction_from_text,
    _urls_match,
    detect_transaction_type,
    extract_listing_price,
    get_main_content_root,
    is_in_excluded_zone,
    is_price_per_m2_snippet,
    parse_euro_amount,
    RENT_HINT_RE,
    SALE_HINT_RE,
    SURFACE_RE,
    PRICE_RE,
)
from crawler.hub_detection import is_hub_listing_address

DOMAIN_TITLE_SELECTORS: dict[str, list[str]] = {
    "leboncoin": ["[data-qa-id*='adview_title']", "h1"],
    "pap": [".item-title", "h1"],
    "seloger": ["[data-testid='ad-title']", "h1"],
    "bienici": [".adSummaryTitle", "[data-test='title']", "h1"],
    "logic-immo": [".property-title", "h1"],
    "paruvendu": [".detail-title", "h1"],
    "lefigaro": [".titre-annonce", "h1"],
}

MIN_SOURCES_FOR_CONSENSUS = 2
PRICE_TOLERANCE_PCT = 0.04
SURFACE_TOLERANCE_M2 = 2.0
DATE_TOLERANCE_DAYS = 4

HERO_PRICE_RE = re.compile(
    r"(\d{1,3}(?:[\s\u00a0.]\d{3})+|\d{4,})\s*(?:€|eur\b|euros?\b)",
    re.IGNORECASE,
)


@dataclass
class FactCandidate:
    field: str
    value: Any
    source: str
    score: int = 0


@dataclass
class FactsAudit:
    title: str | None = None
    price: int | None = None
    surface: float | None = None
    published_at: str | None = None
    transaction_type: str | None = None
    checks_passed: list[str] = field(default_factory=list)
    checks_failed: list[str] = field(default_factory=list)
    sources: dict[str, list[str]] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return not self.checks_failed

    def to_dict(self) -> dict[str, Any]:
        return {
            "title": self.title,
            "price": self.price,
            "surface": self.surface,
            "published_at": self.published_at,
            "transaction_type": self.transaction_type,
            "checks_passed": self.checks_passed,
            "checks_failed": self.checks_failed,
            "sources": self.sources,
        }


def _clean_title(text: str) -> str:
    t = re.sub(r"\s+", " ", (text or "").strip())
    t = re.sub(r"\s*[|\-–—]\s*(?:LeBonCoin|PAP|SeLoger|Bien.?ici|ParuVendu|Figaro).*$", "", t, flags=re.I)
    return t[:300]


def _collect_title_candidates(soup: BeautifulSoup, page_url: str, main) -> list[FactCandidate]:
    out: list[FactCandidate] = []
    domain = _domain_key_from_url(page_url)

    for sel in DOMAIN_TITLE_SELECTORS.get(domain, ["h1"]):
        el = _pick_listing_element(main, [sel]) or main.select_one(sel)
        if not el or is_in_excluded_zone(el):
            continue
        text = _clean_title(el.get_text(" ", strip=True))
        if len(text) >= 8 and not is_hub_listing_address(text):
            out.append(FactCandidate("title", text, f"dom:{sel}", 55))

    hero = _get_hero_block(main)
    h1 = hero.select_one("h1")
    if h1:
        text = _clean_title(h1.get_text(" ", strip=True))
        if len(text) >= 8 and not is_hub_listing_address(text):
            out.append(FactCandidate("title", text, "dom:h1-hero", 60))

    for meta in soup.find_all("meta"):
        prop = (meta.get("property") or meta.get("name") or "").lower()
        if prop in ("og:title", "twitter:title"):
            text = _clean_title(meta.get("content") or "")
            if len(text) >= 8 and not is_hub_listing_address(text):
                out.append(FactCandidate("title", text, f"meta:{prop}", 45))

    for script in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(script.string or "")
        except (json.JSONDecodeError, TypeError):
            continue
        name = _json_ld_listing_name(data, page_url)
        if name:
            out.append(FactCandidate("title", _clean_title(name), "json-ld:name", 50))

    return out


def _json_ld_listing_name(data: Any, page_url: str) -> str | None:
    from crawler.extractors import _urls_match

    if isinstance(data, list):
        for item in data:
            n = _json_ld_listing_name(item, page_url)
            if n:
                return n
        return None
    if not isinstance(data, dict):
        return None
    url = str(data.get("url") or data.get("@id") or "")
    if page_url and url and not _urls_match(page_url, url):
        if data.get("name") and "address" not in data:
            pass
        elif url.startswith("http"):
            return None
    name = data.get("name") or data.get("headline")
    if isinstance(name, str) and len(name.strip()) >= 8:
        return name.strip()
    for key in ("@graph", "mainEntity"):
        if key in data:
            n = _json_ld_listing_name(data[key], page_url)
            if n:
                return n
    return None


def _collect_price_candidates(soup: BeautifulSoup, page_url: str, main) -> list[FactCandidate]:
    out: list[FactCandidate] = []
    tx_default = detect_transaction_type(soup, page_url)
    hero = _get_hero_text(main, 4000)

    info = extract_listing_price(soup, page_url)
    if info:
        out.append(
            FactCandidate(
                "price",
                info.amount,
                "extract:primary",
                70,
            )
        )
        out.append(
            FactCandidate(
                "transaction",
                info.transaction,
                "extract:primary",
                70,
            )
        )

    for el in _get_hero_block(main).select(
        '[itemprop="price"], [data-qa-id*="price"], [data-testid*="price"], [data-test*="price"]'
    ):
        if _is_false_price_element(el):
            continue
        ctx = el.get_text(" ", strip=True)
        tx = _transaction_from_text(ctx) if RENT_HINT_RE.search(ctx) else tx_default
        amount = parse_euro_amount(el.get("content") or ctx, transaction=tx)
        if amount:
            out.append(FactCandidate("price", amount, "dom:price-hero", 65))
            out.append(FactCandidate("transaction", tx, "dom:price-hero", 60))

    for script in soup.find_all("script", type="application/ld+json"):
        try:
            blob = json.loads(script.string or "")
        except (json.JSONDecodeError, TypeError):
            continue
        price, tx = _json_ld_price(blob, page_url)
        if price:
            out.append(FactCandidate("price", price, "json-ld:price", 55))
        if tx:
            out.append(FactCandidate("transaction", tx, "json-ld:price", 50))

    seen: set[int] = set()
    for m in PRICE_RE.finditer(hero):
        snippet = hero[max(0, m.start() - 40) : m.end() + 40]
        if _price_match_is_per_m2(hero, m):
            continue
        tx = _transaction_from_text(snippet) if RENT_HINT_RE.search(snippet) else tx_default
        amount = parse_euro_amount(m.group(0), transaction=tx)
        if amount and amount not in seen:
            seen.add(amount)
            out.append(FactCandidate("price", amount, "hero:regex", 25))

    return out


def _json_ld_price(data: Any, page_url: str) -> tuple[int | None, str | None]:
    from crawler.extractors import _urls_match

    if isinstance(data, list):
        for item in data:
            p, t = _json_ld_price(item, page_url)
            if p:
                return p, t
        return None, None
    if not isinstance(data, dict):
        return None, None
    url = str(data.get("url") or data.get("@id") or "")
    if page_url and url.startswith("http") and not _urls_match(page_url, url):
        for key in ("@graph", "mainEntity"):
            if key in data:
                return _json_ld_price(data[key], page_url)
        return None, None
    offers = data.get("offers") or data.get("offer")
    if isinstance(offers, list) and offers:
        offers = offers[0]
    if isinstance(offers, dict):
        raw = offers.get("price") or offers.get("lowPrice")
        unit = str(offers.get("priceCurrency") or "") + str(offers.get("unitText") or "")
        tx = "location" if RENT_HINT_RE.search(unit) else "vente"
        if raw is not None:
            p = parse_euro_amount(str(raw), transaction=tx)
            if p:
                return p, tx
    for key in ("@graph", "mainEntity"):
        if key in data:
            return _json_ld_price(data[key], page_url)
    return None, None


def _collect_surface_candidates(soup: BeautifulSoup, page_url: str, main) -> list[FactCandidate]:
    from crawler.extractors import DOMAIN_SURFACE_SELECTORS, extract_listing_surface

    out: list[FactCandidate] = []
    val = extract_listing_surface(soup, page_url)
    if val is not None:
        out.append(FactCandidate("surface", val, "extract:primary", 70))

    domain = _domain_key_from_url(page_url)
    for sel in DOMAIN_SURFACE_SELECTORS.get(domain, []):
        el = _pick_listing_element(main, [sel])
        if not el:
            continue
        v = _parse_surface_value(el.get_text(" ", strip=True))
        if v:
            out.append(FactCandidate("surface", v, f"dom:{sel}", 60))

    hero = _get_hero_text(main, 5000)
    seen: set[float] = set()
    for m in SURFACE_RE.finditer(hero):
        v = _parse_surface_value(m.group(0))
        if v is not None and v not in seen:
            seen.add(v)
            out.append(FactCandidate("surface", v, "hero:regex", 30))

    for script in soup.find_all("script", type="application/ld+json"):
        try:
            blob = json.loads(script.string or "")
        except (json.JSONDecodeError, TypeError):
            continue
        v = _json_ld_surface(blob)
        if v:
            out.append(FactCandidate("surface", v, "json-ld:floorSize", 55))

    return out


def _json_ld_surface(data: Any) -> float | None:
    if isinstance(data, list):
        for item in data:
            v = _json_ld_surface(item)
            if v:
                return v
        return None
    if not isinstance(data, dict):
        return None
    fs = data.get("floorSize")
    if isinstance(fs, dict):
        fs = fs.get("value") or fs.get("@value")
    if fs is not None:
        return _parse_surface_value(str(fs))
    for key in ("@graph", "mainEntity"):
        if key in data:
            v = _json_ld_surface(data[key])
            if v:
                return v
    return None


def _collect_date_candidates(soup: BeautifulSoup, page_url: str, main) -> list[FactCandidate]:
    from crawler.extractors import extract_listing_published_date

    out: list[FactCandidate] = []
    val = extract_listing_published_date(soup, page_url)
    if val:
        out.append(FactCandidate("published_at", val, "extract:primary", 70))

    for meta in soup.find_all("meta"):
        prop = (meta.get("property") or meta.get("name") or "").lower()
        if prop in ("article:published_time", "og:published_time", "datepublished"):
            parsed = _parse_date_string(meta.get("content") or "")
            if parsed:
                out.append(FactCandidate("published_at", parsed, f"meta:{prop}", 55))

    for el in _get_hero_block(main).select("time[datetime]"):
        if is_in_excluded_zone(el):
            continue
        parsed = _parse_date_string(el.get("datetime") or el.get_text(" ", strip=True))
        if parsed:
            out.append(FactCandidate("published_at", parsed, "dom:time-hero", 50))

    for script in soup.find_all("script", type="application/ld+json"):
        try:
            blob = json.loads(script.string or "")
        except (json.JSONDecodeError, TypeError):
            continue
        for key in ("datePosted", "datePublished", "uploadDate"):
            found = _find_json_date(blob, key)
            if found:
                out.append(FactCandidate("published_at", found, f"json-ld:{key}", 52))

    return out


def collect_listing_fact_candidates(
    soup: BeautifulSoup,
    page_url: str = "",
) -> list[FactCandidate]:
    main = get_main_content_root(soup, page_url)
    out: list[FactCandidate] = []
    out.extend(_collect_title_candidates(soup, page_url, main))
    out.extend(_collect_price_candidates(soup, page_url, main))
    out.extend(_collect_surface_candidates(soup, page_url, main))
    out.extend(_collect_date_candidates(soup, page_url, main))
    return out


def _numeric_clusters(values: list[tuple[int, float]]) -> list[list[tuple[int, float]]]:
    """Regroupe valeurs proches (score, value)."""
    if not values:
        return []
    sorted_vals = sorted(values, key=lambda x: x[1])
    clusters: list[list[tuple[int, float]]] = [[sorted_vals[0]]]
    for score, val in sorted_vals[1:]:
        ref = clusters[-1][0][1]
        tol = max(ref * PRICE_TOLERANCE_PCT, 500 if ref > 10_000 else 50)
        if abs(val - ref) <= tol:
            clusters[-1].append((score, val))
        else:
            clusters.append([(score, val)])
    return clusters


def _surface_clusters(values: list[tuple[int, float]]) -> list[list[tuple[int, float]]]:
    if not values:
        return []
    sorted_vals = sorted(values, key=lambda x: x[1])
    clusters: list[list[tuple[int, float]]] = [[sorted_vals[0]]]
    for score, val in sorted_vals[1:]:
        ref = clusters[-1][0][1]
        if abs(val - ref) <= SURFACE_TOLERANCE_M2:
            clusters[-1].append((score, val))
        else:
            clusters.append([(score, val)])
    return clusters


def _date_clusters(values: list[tuple[int, str]]) -> list[list[tuple[int, str]]]:
    if not values:
        return []
    parsed: list[tuple[int, date, str]] = []
    for score, iso in values:
        m = re.match(r"^(\d{4})-(\d{2})-(\d{2})$", iso)
        if not m:
            continue
        try:
            d = date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        except ValueError:
            continue
        parsed.append((score, d, iso))
    if not parsed:
        return []
    parsed.sort(key=lambda x: x[1])
    clusters: list[list[tuple[int, str]]] = [[(parsed[0][0], parsed[0][2])]]
    for score, d, iso in parsed[1:]:
        ref = date.fromisoformat(clusters[-1][0][1])
        if abs((d - ref).days) <= DATE_TOLERANCE_DAYS:
            clusters[-1].append((score, iso))
        else:
            clusters.append([(score, iso)])
    return clusters


def _pick_cluster(clusters: list[list[tuple[int, float]]]) -> tuple[float | None, bool, str]:
    if not clusters:
        return None, False, "aucune valeur"
    best = max(clusters, key=lambda c: (len(c), sum(s for s, _ in c)))
    if len(clusters) > 1:
        second = sorted(clusters, key=lambda c: (len(c), sum(s for s, _ in c)), reverse=True)[1]
        if len(second) >= 2 and len(best) < 2:
            return None, False, "valeurs contradictoires (plusieurs annonces)"
        if len(best) == 1 and len(second) >= 1:
            v1 = best[0][1]
            v2 = second[0][1]
            if v1 and v2 and abs(v1 - v2) / max(v1, v2, 1) > 0.12:
                return None, False, "valeurs contradictoires (plusieurs annonces)"
    val = round(sum(v for _, v in best) / len(best), 2 if any(v < 1000 for _, v in best) else 0)
    if isinstance(val, float) and val == int(val):
        val = int(val)
    consensus = len(best) >= MIN_SOURCES_FOR_CONSENSUS or best[0][0] >= 65
    return val, consensus, ""


def _pick_date_cluster(clusters: list[list[tuple[int, str]]]) -> tuple[str | None, bool, str]:
    if not clusters:
        return None, False, "aucune date"
    best = max(clusters, key=lambda c: (len(c), sum(s for s, _ in c)))
    if len(clusters) > 1 and len(best) < 2:
        return None, False, "dates contradictoires"
    iso = best[0][1]
    consensus = len(best) >= MIN_SOURCES_FOR_CONSENSUS or best[0][0] >= 65
    return iso, consensus, ""


def _pick_title(candidates: list[FactCandidate]) -> tuple[str | None, bool, str]:
    titles = [c for c in candidates if c.field == "title"]
    if not titles:
        return None, False, "titre manquant"
    titles.sort(key=lambda c: -c.score)
    best = titles[0]
    similar = sum(
        1
        for t in titles[1:]
        if _titles_similar(best.value, t.value)
    )
    if similar >= 1 or best.score >= 55:
        return best.value, True, ""
    return best.value, False, "titre non confirmé par une 2e source"


def _titles_similar(a: str, b: str) -> bool:
    if not a or not b:
        return False
    a_l, b_l = a.lower(), b.lower()
    if a_l == b_l:
        return True
    if a_l in b_l or b_l in a_l:
        return True
    wa = set(re.findall(r"\w{4,}", a_l))
    wb = set(re.findall(r"\w{4,}", b_l))
    if not wa or not wb:
        return False
    return len(wa & wb) / min(len(wa), len(wb)) >= 0.5


def _title_matches_metrics(title: str | None, surface: float | None, price: int | None) -> tuple[bool, str]:
    """Vérifie que le titre ne contient pas des m²/prix différents de la fiche confirmée."""
    if not title:
        return True, ""
    for m in SURFACE_RE.finditer(title):
        v = _parse_surface_value(m.group(0))
        if v is not None and surface is not None and abs(v - surface) > SURFACE_TOLERANCE_M2:
            return False, f"surface dans le titre ({v:g} m²) ≠ surface confirmée ({surface:g} m²)"
    if price is not None:
        t_compact = re.sub(r"\s", "", title.lower())
        for m in PRICE_RE.finditer(title):
            snippet = title[max(0, m.start() - 20) : m.end() + 20]
            if is_price_per_m2_snippet(snippet):
                continue
            tx = "location" if RENT_HINT_RE.search(snippet) else "vente"
            amt = parse_euro_amount(m.group(0), transaction=tx)
            if amt and abs(amt - price) / max(price, 1) > PRICE_TOLERANCE_PCT:
                return False, f"prix dans le titre ({amt:,} €) ≠ prix confirmé ({price:,} €)".replace(",", " ")
    return True, ""


def verify_listing_page_identity(soup: BeautifulSoup, page_url: str) -> tuple[bool, str]:
    """Vérifie que la page HTML correspond bien à l'URL crawlée."""
    if not page_url:
        return True, ""

    canonical = soup.find("link", rel=lambda v: v and "canonical" in str(v).lower())
    if canonical and canonical.get("href"):
        href = canonical["href"].strip()
        if href.startswith("http") and not _urls_match(page_url, href):
            return False, "URL canonique différente de l'annonce crawlée"

    id_match = re.search(r"(\d{6,})", page_url)
    listing_id = id_match.group(1) if id_match else None

    for script in soup.find_all("script", type="application/ld+json"):
        try:
            blob = json.loads(script.string or "")
        except (json.JSONDecodeError, TypeError):
            continue
        if _json_ld_url_mismatch(blob, page_url, listing_id):
            return False, "JSON-LD pointe vers une autre annonce"

    if listing_id:
        og_url = soup.find("meta", property="og:url")
        og_content = (og_url.get("content") or "") if og_url else ""
        if og_content.startswith("http") and not _urls_match(page_url, og_content):
            return False, "og:url différente de l'annonce crawlée"

    return True, ""


def _json_ld_url_mismatch(data: Any, page_url: str, listing_id: str | None) -> bool:
    if isinstance(data, list):
        return any(_json_ld_url_mismatch(item, page_url, listing_id) for item in data)
    if not isinstance(data, dict):
        return False
    url = str(data.get("url") or data.get("@id") or "")
    if url.startswith("http") and page_url and not _urls_match(page_url, url):
        if data.get("offers") or data.get("offer") or data.get("@type") in (
            "Apartment",
            "House",
            "SingleFamilyResidence",
            "Product",
        ):
            return True
    if listing_id and url and listing_id not in url and listing_id not in page_url:
        pass
    for key in ("@graph", "mainEntity"):
        if key in data and _json_ld_url_mismatch(data[key], page_url, listing_id):
            return True
    return False


def _published_at_ok(iso: str) -> bool:
    m = re.match(r"^(\d{4})-(\d{2})-(\d{2})$", (iso or "").strip())
    if not m:
        return False
    try:
        date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    except ValueError:
        return False
    return True


def verify_and_apply_listing_facts(
    lead: LeadData,
    soup: BeautifulSoup,
    page_url: str = "",
) -> FactsAudit:
    """
    Vérifie titre / m² / prix / date par consensus multi-sources et applique au lead.
    """
    candidates = collect_listing_fact_candidates(soup, page_url)
    audit = FactsAudit()
    audit.sources = {}

    id_ok, id_err = verify_listing_page_identity(soup, page_url)
    if id_ok:
        audit.checks_passed.append("URL/page cohérentes")
    else:
        audit.checks_failed.append(id_err)

    for c in candidates:
        audit.sources.setdefault(c.field, []).append(c.source)

    title, title_ok, title_err = _pick_title([c for c in candidates if c.field == "title"])
    if title:
        audit.title = title
        lead.raw_extras["listing_title"] = title
        if title_ok:
            audit.checks_passed.append("titre confirmé")
        else:
            audit.checks_failed.append(title_err or "titre non confirmé")

    price_cands = [(c.score, float(c.value)) for c in candidates if c.field == "price"]
    price_val, price_ok, price_err = _pick_cluster(_numeric_clusters(price_cands))
    if price_val is not None:
        audit.price = int(price_val)
        if price_ok:
            audit.checks_passed.append("prix confirmé")
            lead.price = int(price_val)
        else:
            audit.checks_failed.append(price_err or "prix non confirmé")
            lead.price = None
    elif lead.price is not None:
        audit.checks_failed.append("prix absent ou non confirmé")
        lead.price = None

    tx_cands = [c for c in candidates if c.field == "transaction"]
    if tx_cands:
        tx_cands.sort(key=lambda c: -c.score)
        audit.transaction_type = tx_cands[0].value
        lead.transaction_type = tx_cands[0].value
        if tx_cands[0].value == "location":
            lead.price_period = "month"
        else:
            lead.price_period = None
        audit.checks_passed.append("transaction déduite")

    surf_cands = [(c.score, float(c.value)) for c in candidates if c.field == "surface"]
    surf_val, surf_ok, surf_err = _pick_cluster(_surface_clusters(surf_cands))
    if surf_val is not None:
        audit.surface = float(surf_val)
        if surf_ok:
            audit.checks_passed.append("surface confirmée")
            lead.surface = float(surf_val)
        else:
            audit.checks_failed.append(surf_err or "surface non confirmée")
            lead.surface = None
    elif lead.surface is not None:
        audit.checks_failed.append("surface absente ou non confirmée")
        lead.surface = None

    date_cands = [(c.score, str(c.value)) for c in candidates if c.field == "published_at"]
    date_val, date_ok, date_err = _pick_date_cluster(_date_clusters(date_cands))
    if date_val:
        audit.published_at = date_val
        if date_ok:
            audit.checks_passed.append("date confirmée")
            lead.published_at = date_val
        else:
            audit.checks_failed.append(date_err or "date non confirmée")
            if not _published_at_ok(date_val):
                lead.published_at = None
    elif lead.published_at:
        audit.checks_failed.append("date absente ou non confirmée")
        lead.published_at = None

    if title and not is_hub_listing_address(title) and (
        not lead.address or is_hub_listing_address(lead.address)
    ):
        lead.address = title[:300]

    if audit.price and audit.surface:
        from crawler.listing_guard import validate_field_coherence

        ok_ratio, ratio_err = validate_field_coherence(lead)
        if ok_ratio:
            audit.checks_passed.append("ratio prix/surface cohérent")
        else:
            audit.checks_failed.append(ratio_err)

    if title and (audit.price or audit.surface):
        ok_title, title_metric_err = _title_matches_metrics(title, audit.surface, audit.price)
        if ok_title:
            audit.checks_passed.append("titre aligné avec prix/surface")
        else:
            audit.checks_failed.append(title_metric_err)

    lead.raw_extras["facts_audit"] = audit.to_dict()
    return audit


def validate_listing_facts_strict(
    lead: LeadData,
    soup: BeautifulSoup | None,
    page_url: str = "",
) -> tuple[bool, str]:
    """Rejette si conflit fort ou champs clés non confirmés."""
    audit_dict = lead.raw_extras.get("facts_audit") or {}
    failed = audit_dict.get("checks_failed") or []
    passed = audit_dict.get("checks_passed") or []

    critical = [f for f in failed if "contradictoire" in f.lower() or "plusieurs annonces" in f.lower()]
    if critical:
        return False, critical[0]

    metric_mismatch = [f for f in failed if "≠" in f or "dans le titre" in f.lower()]
    if metric_mismatch:
        return False, metric_mismatch[0]

    url_issues = [
        f
        for f in failed
        if "url" in f.lower() or "json-ld" in f.lower() or "canonique" in f.lower()
    ]
    if url_issues:
        return False, url_issues[0]

    ratio_issues = [f for f in failed if "incohérent" in f.lower() or "ratio" in f.lower()]
    if ratio_issues:
        return False, ratio_issues[0]

    # Ne pas exiger « prix/surface confirmés » : trop strict en crawl réel
    # (bloquait presque toutes les fiches). On ne rejette que les conflits ci-dessus.

    if soup is not None and not audit_dict:
        audit = verify_and_apply_listing_facts(lead, soup, page_url)
        if not audit.ok and any("contradictoire" in x for x in audit.checks_failed):
            return False, audit.checks_failed[0]

    if lead.price and lead.surface and soup:
        hero_conflict = _hero_has_conflicting_metrics(soup, page_url)
        if hero_conflict:
            fa = lead.raw_extras.get("facts_audit") or {}
            if len(fa.get("checks_failed") or []) >= 2:
                return False, "plusieurs prix/surfaces dans la fiche (mix annonces)"

    return True, ""


def _parse_raw_euro_digits(raw: str) -> int | None:
    """Extrait un montant numérique sans filtre vente/location (détection de conflits)."""
    if not raw:
        return None
    text = str(raw).replace("\xa0", " ")
    if is_price_per_m2_snippet(text):
        text = _strip_combined_price_per_m2(text)
        if not text:
            return None
    if "€" in text:
        text = text.split("€")[0]
    digits = re.sub(r"[^\d]", "", text)
    if not digits or len(digits) > 9:
        return None
    val = int(digits)
    return val if val >= 100 else None


def _hero_has_conflicting_metrics(soup: BeautifulSoup, page_url: str) -> bool:
    main = get_main_content_root(soup, page_url)
    hero_block = _get_hero_block(main)
    hero = _get_hero_text(main, 4500)
    tx = detect_transaction_type(soup, page_url)
    amounts: set[int] = set()
    for el in hero_block.select(
        '[itemprop="price"], [data-qa-id*="price"], [data-testid*="price"], [data-test*="price"]'
    ):
        if _is_false_price_element(el):
            continue
        raw = el.get("content") or el.get_text(" ", strip=True)
        for parser in (_parse_raw_euro_digits, lambda r: parse_euro_amount(r, transaction=tx)):
            amount = parser(raw)
            if amount:
                amounts.add(amount)
                break
    for pattern in (HERO_PRICE_RE, PRICE_RE):
        for m in pattern.finditer(hero):
            if _price_match_is_per_m2(hero, m):
                continue
            raw_amt = _parse_raw_euro_digits(m.group(0))
            if raw_amt:
                amounts.add(raw_amt)
    if len(amounts) >= 2:
        vals = sorted(amounts)
        if vals[-1] / max(vals[0], 1) > 1.15:
            return True
        low = [v for v in vals if v < 20_000]
        high = [v for v in vals if v >= 50_000]
        if low and high:
            return True

    surfaces: set[float] = set()
    for el in hero_block.select('[itemprop="floorSize"], [data-qa-id*="surface"], [data-testid*="surface"]'):
        v = _parse_surface_value(el.get_text(" ", strip=True))
        if v:
            surfaces.add(v)
    for m in SURFACE_RE.finditer(hero):
        v = _parse_surface_value(m.group(0))
        if v:
            surfaces.add(v)
    if len(surfaces) >= 2:
        vals = sorted(surfaces)
        if vals[-1] - vals[0] > 8:
            return True
    return False
