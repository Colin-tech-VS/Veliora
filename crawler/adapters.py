"""Adaptateurs de crawl par portail immobilier."""

from __future__ import annotations

import json
import re
from abc import ABC, abstractmethod
from dataclasses import dataclass
from urllib.parse import urlparse

from crawler.extractors import (
    LeadData,
    extract_from_selectors,
    extract_from_text,
    find_listing_links,
    generic_extract,
    get_main_content_root,
    normalize_phone,
    _get_hero_text,
)
from crawler.config import STREAMESTATE_DISPLAY_NAME
from crawler.portals import resolve_base_portal_id


def _is_bare_homepage(url: str) -> bool:
    """True si l'URL n'est qu'une page d'accueil (pas de chemin/résultats exploitable)."""
    if not url:
        return True
    parsed = urlparse(url)
    path = (parsed.path or "").strip("/")
    return not path and not parsed.query


def _best_search_url(stored: str | None, default: str) -> str:
    """Préfère l'URL de résultats par défaut si la base ne contient qu'une page d'accueil."""
    stored = (stored or "").strip()
    if stored and not _is_bare_homepage(stored):
        return stored
    return default or stored


@dataclass
class AdapterConfig:
    id: str
    name: str
    base_url: str
    search_url: str
    listing_patterns: list[str]
    enabled: bool = True  # False = portail mort/injoignable, exclu du crawl


class BaseAdapter(ABC):
    config: AdapterConfig

    def __init__(self, config: AdapterConfig):
        self.config = config

    @property
    def source_id(self) -> str:
        return self.config.id

    @property
    def source_name(self) -> str:
        return self.config.name

    def find_listings(self, html: str, page_url: str, limit: int = 150) -> list[str]:
        from crawler.config import DISCOVERY_ADAPTIVE_MIN_LINKS_DIV
        from crawler.site_discovery import find_listing_links_adaptive, sort_listing_urls_by_score

        base = self.config.base_url or page_url
        links = find_listing_links_adaptive(
            html,
            page_url,
            base,
            self.config.listing_patterns,
            limit=limit,
        )
        min_links = max(2, limit // max(1, DISCOVERY_ADAPTIVE_MIN_LINKS_DIV))
        if len(links) < min_links:
            pattern_links = find_listing_links(
                html, page_url, self.config.listing_patterns, limit=limit
            )
            links = sort_listing_urls_by_score(
                list(dict.fromkeys(links + pattern_links))
            )[:limit]
        if not links:
            links = find_listing_links(
                html, page_url, GenericAdapter().config.listing_patterns, limit=limit
            )
            links = sort_listing_urls_by_score(links)[:limit]
        return links[:limit]

    def parse_listing(self, html: str, url: str) -> LeadData:
        from bs4 import BeautifulSoup
        from crawler.extractors import (
            apply_listing_classification_to_lead,
            apply_listing_facts_to_lead,
            apply_listing_price_to_lead,
            apply_listing_published_to_lead,
            normalize_listing_url,
        )

        url = normalize_listing_url(url)
        soup = BeautifulSoup(html, "lxml")
        lead = generic_extract(html, url, source=self.config.name)
        lead.source_url = url
        lead = self.enhance_listing(html, url, lead)
        apply_listing_price_to_lead(lead, soup, url)
        apply_listing_facts_to_lead(lead, soup, url)
        apply_listing_published_to_lead(lead, soup, url)
        apply_listing_classification_to_lead(lead, soup, url)
        from crawler.extractors import enrich_core_listing_fields
        from crawler.listing_facts import verify_and_apply_listing_facts

        verify_and_apply_listing_facts(lead, soup, url)
        lead = enrich_core_listing_fields(html, url, lead)
        # Caractéristiques structurées standardisées (héritées par tous les
        # adaptateurs) — alimentent le rapprochement d'adresse en post-processing.
        from crawler.address_match.features import apply_features_to_lead

        apply_features_to_lead(lead, soup, url, html=html)
        return lead

    def enhance_listing(self, html: str, url: str, lead: LeadData) -> LeadData:
        return lead

    def matches_url(self, url: str) -> bool:
        if not self.config.base_url:
            return False
        host = urlparse(url).netloc.lower()
        base_host = urlparse(self.config.base_url).netloc.lower()
        return base_host in host


def _extract_embedded_phones(html: str, lead: LeadData) -> LeadData:
    from crawler.extractors import extract_embedded_phones_from_html

    return extract_embedded_phones_from_html(html, lead)


def _extract_lbc_json(html: str, lead: LeadData) -> LeadData:
    """Parse les blobs JSON embarqués (LeBonCoin / React)."""
    for m in re.finditer(r'<script[^>]*type="application/json"[^>]*>(.*?)</script>', html, re.S):
        blob = m.group(1).strip()
        if len(blob) < 40 or "phone" not in blob.lower():
            continue
        try:
            data = json.loads(blob)
        except json.JSONDecodeError:
            continue

        def walk(obj):
            if isinstance(obj, dict):
                for k, v in obj.items():
                    kl = k.lower()
                    if kl in ("phone", "phonenumber", "phone_number") and isinstance(v, str):
                        p = normalize_phone(v)
                        if p and len(re.sub(r"\D", "", p)) >= 10:
                            lead.phone = p
                    elif kl in ("ownername", "sellername", "name") and isinstance(v, str) and not lead.first_name:
                        from crawler.extractors import split_name
                        from crawler.hub_detection import is_listing_title_name
                        fn, ln = split_name(v)
                        if not is_listing_title_name(fn, ln):
                            lead.first_name = lead.first_name or fn
                            lead.last_name = lead.last_name or ln
                    else:
                        walk(v)
            elif isinstance(obj, list):
                for item in obj:
                    walk(item)

        walk(data)
        if lead.phone:
            break
    return lead


class LeboncoinAdapter(BaseAdapter):
    def enhance_listing(self, html: str, url: str, lead: LeadData) -> LeadData:
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(html, "lxml")
        lead = extract_from_selectors(
            soup,
            {
                "address": "[data-qa-id=adview_location_informations], [data-qa-id*='location']",
                "surface": "[data-qa-id=criteria_item_surface], [data-qa-id*='surface']",
                "price": "[data-qa-id=adview_price], [data-qa-id*='price']",
                "name": (
                    "[data-qa-id=adview_contact_container] span, "
                    "[data-qa-id*='seller'], [data-qa-id*='owner']"
                ),
                "phone": (
                    "a[href^='tel:'], [data-qa-id*='phone'], "
                    "[data-test-id*='phone'], button[data-qa-id*='phone']"
                ),
                "email": "a[href^='mailto:']",
            },
            lead,
            overwrite=True,
            page_url=url,
        )
        lead = _extract_lbc_json(html, lead)
        lead = _extract_embedded_phones(html, lead)
        main = get_main_content_root(soup, url)
        return extract_from_text(_get_hero_text(main), lead)


class PapAdapter(BaseAdapter):
    def enhance_listing(self, html: str, url: str, lead: LeadData) -> LeadData:
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(html, "lxml")
        lead = extract_from_selectors(
            soup,
            {
                "address": ".item-title, .item-localisation, h1",
                "surface": ".item-caracteristiques, .surface",
                "price": ".item-price, .prix",
                "name": ".owner-name, .contact-name, .nom-annonceur",
                "phone": "a[href^='tel:']",
                "email": "a[href^='mailto:']",
            },
            lead,
            overwrite=True,
            page_url=url,
        )
        return _extract_embedded_phones(html, lead)


class SelogerAdapter(BaseAdapter):
    def enhance_listing(self, html: str, url: str, lead: LeadData) -> LeadData:
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(html, "lxml")
        lead = extract_from_selectors(
            soup,
            {
                "address": "[data-testid=ad-address], .Title__Address, h1",
                "surface": "[data-testid=ad-surface], [data-testid*='surface']",
                "price": "[data-testid=price], [data-testid*='price']",
                "name": "[data-testid=contact-name], [data-testid*='seller']",
                "phone": "a[href^='tel:'], [data-testid*='phone']",
                "email": "a[href^='mailto:']",
            },
            lead,
            overwrite=True,
            page_url=url,
        )
        return _extract_embedded_phones(html, lead)


class LogicImmoAdapter(BaseAdapter):
    def enhance_listing(self, html: str, url: str, lead: LeadData) -> LeadData:
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(html, "lxml")
        lead = extract_from_selectors(
            soup,
            {
                "address": ".property-address, .annonceTitre, h1",
                "surface": ".property-surface, .criterion-area",
                "price": ".property-price, .price",
                "name": ".agent-name, .contact-name",
                "phone": "a[href^='tel:']",
                "email": "a[href^='mailto:']",
            },
            lead,
            overwrite=True,
            page_url=url,
        )
        return _extract_embedded_phones(html, lead)


class BienIciAdapter(BaseAdapter):
    def enhance_listing(self, html: str, url: str, lead: LeadData) -> LeadData:
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(html, "lxml")
        lead = extract_from_selectors(
            soup,
            {
                "address": ".adSummaryAddress, [data-test=address], h1",
                "surface": ".adSummarySurface, [data-test=surface]",
                "price": ".adSummaryPrice, [data-test=price]",
                "name": ".adContactName, [data-test=contact-name], .adContactAgency",
                "phone": "a[href^='tel:'], [data-test*='phone']",
                "email": "a[href^='mailto:']",
            },
            lead,
            overwrite=True,
            page_url=url,
        )
        return _extract_embedded_phones(html, lead)


class ParuVenduAdapter(BaseAdapter):
    def enhance_listing(self, html: str, url: str, lead: LeadData) -> LeadData:
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(html, "lxml")
        lead = extract_from_selectors(
            soup,
            {
                "address": ".detail-title, [itemprop=address], h1",
                "surface": ".criteria-surface, .surface",
                "price": ".detail-price, .prix",
                "name": ".seller-name, .contact-name",
                "phone": "a[href^='tel:']",
                "email": "a[href^='mailto:']",
            },
            lead,
            overwrite=True,
            page_url=url,
        )
        return _extract_embedded_phones(html, lead)


class LeFigaroAdapter(BaseAdapter):
    def enhance_listing(self, html: str, url: str, lead: LeadData) -> LeadData:
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(html, "lxml")
        lead = extract_from_selectors(
            soup,
            {
                "address": ".adresse-annonce, [itemprop=address], .listing-address",
                "surface": ".surface-annonce, .listing-surface",
                "price": ".price-annonce, .listing-price, .price",
                "name": ".annonceur-nom, .seller-name, .contact-name",
                "phone": "a[href^='tel:']",
                "email": "a[href^='mailto:']",
            },
            lead,
            overwrite=True,
            page_url=url,
        )
        return _extract_embedded_phones(html, lead)


class GenericAdapter(BaseAdapter):
    """S'adapte automatiquement à n'importe quel site immobilier."""

    def __init__(self, config: AdapterConfig | None = None):
        super().__init__(config or AdapterConfig(
            id="custom",
            name="Site personnalisé",
            base_url="",
            search_url="",
        listing_patterns=[
            r"/annonce",
            r"/detail",
            r"/listing",
            r"/property",
            r"/ad/\d",
            r"/bien[^/\"'\s]*/\d{4,}",
            r"/annonces/[^/\"'\s]+-\d{4,}",
            r"[\-_/]\d{6,}\.html?",
            r"immobilier\.lefigaro\.fr/annonces/annonce-[^/\"'\s]+",
            r"immobilier\.lefigaro\.fr/annonces/[^/\"'\s]+-\d{7,}\.html",
        ],
        ))

    def matches_url(self, url: str) -> bool:
        return True

    def parse_listing(self, html: str, url: str) -> LeadData:
        from crawler.url_utils import display_name_from_domain

        host = urlparse(url).netloc.replace("www.", "")
        if not (self.config.name or "").strip() or self.config.name == "Site personnalisé":
            old_name = self.config.name
            self.config.name = display_name_from_domain(host) or "Custom"
            try:
                return super().parse_listing(html, url)
            finally:
                self.config.name = old_name
        return super().parse_listing(html, url)


DEFAULT_SOURCES: list[AdapterConfig] = [
    AdapterConfig(
        id="leboncoin",
        name="LeBonCoin",
        base_url="https://www.leboncoin.fr",
        search_url="https://www.leboncoin.fr/recherche?category=9&real_estate_type=2",
        listing_patterns=[
            r"leboncoin\.fr/ad/ventes_immobilieres/\d+",
            r"leboncoin\.fr/ad/ventes_[^/\"'\s]+/\d+",
            r"leboncoin\.fr/ad/locations/\d+",
            r"leboncoin\.fr/ad/locations_[^/\"'\s]+/\d+",
            r"leboncoin\.fr/\d+\.htm",
            r"leboncoin\.fr/ad/\d+",
        ],
    ),
    AdapterConfig(
        id="pap",
        name="PAP",
        base_url="https://www.pap.fr",
        search_url="https://www.pap.fr/annonce/vente-appartements",
        listing_patterns=[r"pap\.fr/annonce/vente-[^/\"'\s]+-\d+", r"pap\.fr/annonces/"],
    ),
    AdapterConfig(
        id="seloger",
        name="SeLoger",
        base_url="https://www.seloger.com",
        search_url="https://www.seloger.com/list.htm?types=1&projects=2",
        listing_patterns=[
            r"seloger\.com/annonces/achat/",
            r"seloger\.com/detail/",
            r"seloger\.com/annonce/",
        ],
    ),
    AdapterConfig(
        id="logicimmo",
        name="LogicImmo",
        base_url="https://www.logic-immo.com",
        search_url="https://www.logic-immo.com/vente-appartement",
        listing_patterns=[r"logic-immo\.com/detail-vente-", r"logic-immo\.com/annonce/"],
    ),
    AdapterConfig(
        id="bienici",
        name="BienIci",
        base_url="https://www.bienici.com",
        search_url="https://www.bienici.com/recherche/achat/appartement",
        listing_patterns=[r"bienici\.com/annonce/[^/\"'\s]+"],
    ),
    AdapterConfig(
        id="paruvendu",
        name="ParuVendu",
        base_url="https://www.paruvendu.fr",
        search_url="https://www.paruvendu.fr/immobilier/",
        listing_patterns=[
            r"paruvendu\.fr/immobilier/[^?\s\"']+",
            r"paruvendu\.fr/a/[\w/-]+",
            r"paruvendu\.fr/f/immobilier/[\w/-]+",
            r"paruvendu\.fr/[\w/-]+\d{4,}",
        ],
    ),
    AdapterConfig(
        id="lefigaro",
        name="Le Figaro Immobilier",
        base_url="https://immobilier.lefigaro.fr",
        search_url="https://immobilier.lefigaro.fr/annonces/immobilier-vente-bien-france.html",
        listing_patterns=[
            r"immobilier\.lefigaro\.fr/annonces/annonce-[^/\"'\s]+",
            r"immobilier\.lefigaro\.fr/annonces/[^/\"'\s]+-\d{7,}\.html",
        ],
    ),
    # Portails recommandés (HTTP, sans anti-bot — inclus dans « Crawler tout »)
    AdapterConfig(
        id="superimmo",
        name="Superimmo",
        base_url="https://www.superimmo.com",
        search_url="https://www.superimmo.com/achat/appartement",
        listing_patterns=[
            r"superimmo\.com/[^/\"'\s]+/annonce-\d+",
            r"superimmo\.com/annonce/\d+",
            r"superimmo\.com/[^/\"'\s]+/\d{5,}",
        ],
    ),
    AdapterConfig(
        id="avendrealouer",
        name="AvendreAouer",
        base_url="https://www.avendrealouer.fr",
        search_url="https://www.avendrealouer.fr/vente/appartement.html",
        enabled=False,  # toutes les URLs renvoient 404 (SPA / schéma inconnu)
        listing_patterns=[
            r"avendrealouer\.fr/[^/\"'\s]+-\d{5,}\.htm",
            r"avendrealouer\.fr/vente/[^/\"'\s]+-\d+",
        ],
    ),
    AdapterConfig(
        id="etreproprio",
        name="EtreProprio",
        base_url="https://www.etreproprio.com",
        search_url="https://www.etreproprio.com/annonces/vente",
        listing_patterns=[
            r"etreproprio\.com/annonce/\d+",
            r"etreproprio\.com/[^/\"'\s]+/\d{5,}",
        ],
    ),
    AdapterConfig(
        id="maisonappart",
        name="Maison & Appartement",
        base_url="https://www.maison-et-appartement.fr",
        search_url="https://www.maison-et-appartement.fr/vente-appartement",
        enabled=False,  # domaine mort (DNS ne résout plus)
        listing_patterns=[
            r"maison-et-appartement\.fr/[^/\"'\s]+-\d{5,}\.html",
            r"maison-et-appartement\.fr/annonce/\d+",
        ],
    ),
    AdapterConfig(
        id="ouestfranceimmo",
        name="Ouest-France Immo",
        base_url="https://www.ouestfrance-immo.com",
        search_url="https://www.ouestfrance-immo.com/immobilier/vente/",
        listing_patterns=[
            r"ouestfrance-immo\.com/immobilier/vente/[^\"'\s]+/[^\"'\s]+",
            r"ouestfrance-immo\.com/[^/\"'\s]+-\d{5,}",
        ],
    ),
    AdapterConfig(
        id="lesiteimmo",
        name="LeSiteImmo",
        base_url="https://www.lesiteimmo.com",
        search_url="https://www.lesiteimmo.com/acheter/appartement",
        listing_patterns=[
            r"lesiteimmo\.com/acheter/[^\"'\s]+/[^\"'\s]+/\d{5,}",
        ],
    ),
    AdapterConfig(
        id="streamestate",
        name=STREAMESTATE_DISPLAY_NAME,
        base_url="https://www.veliora.fr",
        search_url="https://www.veliora.fr/analyse-approfondie",
        listing_patterns=[
            r"stream\.estate/property/[a-f0-9-]+",
        ],
    ),
    AdapterConfig(
        id="notaires",
        name="Immobilier Notaires",
        base_url="https://www.immobilier.notaires.fr",
        search_url="https://www.immobilier.notaires.fr/fr/ventes",
        enabled=False,  # recherche en JS (toutes les pages liste renvoient 404)
        listing_patterns=[
            r"immobilier\.notaires\.fr/fr/annonce/\d+",
            r"immobilier\.notaires\.fr/[^/\"'\s]+/\d{5,}",
        ],
    ),
]

ADAPTER_CLASSES = {
    "leboncoin": LeboncoinAdapter,
    "pap": PapAdapter,
    "seloger": SelogerAdapter,
    "logicimmo": LogicImmoAdapter,
    "bienici": BienIciAdapter,
    "paruvendu": ParuVenduAdapter,
    "lefigaro": LeFigaroAdapter,
    "superimmo": GenericAdapter,
    "avendrealouer": GenericAdapter,
    "etreproprio": GenericAdapter,
    "maisonappart": GenericAdapter,
    "ouestfranceimmo": GenericAdapter,
    "lesiteimmo": GenericAdapter,
    "streamestate": GenericAdapter,
    "notaires": GenericAdapter,
}

_DEFAULT_BY_ID = {c.id: c for c in DEFAULT_SOURCES}


def build_adapters(db_sources: list[dict] | None = None) -> dict[str, BaseAdapter]:
    """
    Une entrée par source DB (ID scoped `{agency_id}_leboncoin`).
    Utilise le bon adaptateur portail + URLs de la base.
    """
    adapters: dict[str, BaseAdapter] = {}
    db_list = db_sources or []

    for src in db_list:
        sid = src["id"]
        base = resolve_base_portal_id(sid)
        default_cfg = _DEFAULT_BY_ID.get(base) if base else None

        if default_cfg:
            cfg = AdapterConfig(
                id=sid,
                name=src.get("name") or default_cfg.name,
                base_url=src.get("base_url") or default_cfg.base_url,
                search_url=_best_search_url(src.get("search_url"), default_cfg.search_url),
                listing_patterns=list(default_cfg.listing_patterns),
            )
            cls = ADAPTER_CLASSES[base]
        else:
            from crawler.immobilier_catalog import (
                catalog_site_for_id,
                resolve_catalog_id,
            )

            cat_id = resolve_catalog_id(sid)
            cat_site = catalog_site_for_id(cat_id) if cat_id else None
            if cat_site:
                patterns = list(
                    dict.fromkeys(
                        list(cat_site.listing_patterns) + GenericAdapter().config.listing_patterns
                    )
                )[:45]
                cfg = AdapterConfig(
                    id=sid,
                    name=src.get("name") or cat_site.name,
                    base_url=src.get("base_url") or cat_site.base_url,
                    search_url=_best_search_url(src.get("search_url"), cat_site.search_url),
                    listing_patterns=patterns,
                )
                cls = GenericAdapter
                adapters[sid] = cls(cfg)
                continue
            generic_patterns = GenericAdapter().config.listing_patterns
            search_url = src.get("search_url") or src.get("base_url") or ""
            cfg = AdapterConfig(
                id=sid,
                name=src.get("name") or "Site personnalisé",
                base_url=src.get("base_url") or "",
                search_url=search_url,
                listing_patterns=generic_patterns,
            )
            cls = GenericAdapter
            host = urlparse(search_url).netloc.lower().replace("www.", "")
            from crawler.host_discovery import extra_patterns_for_host

            extra = extra_patterns_for_host(
                src.get("base_url") or "",
                search_url,
            )
            if extra:
                cfg.listing_patterns = list(
                    dict.fromkeys(list(cfg.listing_patterns) + extra)
                )[:45]
            for dc in DEFAULT_SOURCES:
                dc_host = urlparse(dc.base_url).netloc.lower().replace("www.", "")
                if dc_host and dc_host in host:
                    cfg = AdapterConfig(
                        id=sid,
                        name=src.get("name") or dc.name,
                        base_url=src.get("base_url") or dc.base_url,
                        search_url=_best_search_url(src.get("search_url"), dc.search_url),
                        listing_patterns=list(dc.listing_patterns),
                    )
                    cls = ADAPTER_CLASSES.get(dc.id, GenericAdapter)
                    break

        adapters[sid] = cls(cfg)

    if not db_list:
        for cfg in DEFAULT_SOURCES:
            cls = ADAPTER_CLASSES.get(cfg.id, BaseAdapter)
            adapters[cfg.id] = cls(cfg)

    adapters["custom"] = GenericAdapter()
    return adapters


def resolve_adapter(url: str, adapters: dict[str, BaseAdapter]) -> BaseAdapter:
    for adapter in adapters.values():
        if adapter.source_id == "custom":
            continue
        if adapter.matches_url(url):
            return adapter
    return adapters["custom"]
