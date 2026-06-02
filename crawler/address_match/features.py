"""Extraction standardisée des caractéristiques structurées d'une annonce.

Ce module est volontairement **sans I/O réseau** : il ne lit que le HTML déjà
récupéré par le crawler. Il est appelé dans `BaseAdapter.parse_listing` (donc
hérité par TOUS les adaptateurs, actuels et futurs) et range le résultat dans
`lead.raw_extras["listing_features"]`.

La résolution d'adresse (croisement DPE / DVF / cadastre / BAN) se fait ensuite
en post-processing, à partir de ces features (voir `resolver.py`).
"""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from typing import Any

from bs4 import BeautifulSoup

# ── Regex caractéristiques ────────────────────────────────────────────────
_SURFACE_HAB_RE = re.compile(
    r"(\d{1,4}(?:[.,]\d{1,2})?)\s*m[²2]\b(?!\s*(?:de\s+)?terrain)", re.I
)
_SURFACE_TERRAIN_RE = re.compile(
    r"terrain[^.\d]{0,30}?(\d{2,6}(?:[.,]\d{1,2})?)\s*m[²2]?|"
    r"(\d{2,6}(?:[.,]\d{1,2})?)\s*m[²2]?\s*(?:de\s+)?terrain",
    re.I,
)
_ROOMS_RE = re.compile(r"\b[tfTF]\s?(\d{1,2})\b|(\d{1,2})\s*pi[eè]ces?\b", re.I)
_BEDROOMS_RE = re.compile(r"(\d{1,2})\s*chambres?\b", re.I)
_FLOOR_RE = re.compile(
    r"(\d{1,2})\s*(?:er|e|ème|eme)?\s*[ée]tage|[ée]tage\s*(?:n[°o]?\s*)?(\d{1,2})", re.I
)
_FLOORS_TOTAL_RE = re.compile(r"sur\s*(\d{1,2})\s*[ée]tages?|immeuble.{0,12}?(\d{1,2})\s*[ée]tages?", re.I)
_YEAR_RE = re.compile(r"(?:construit|construction|b[âa]ti)\D{0,12}(19\d{2}|20\d{2})", re.I)
_REF_RE = re.compile(r"(?:r[ée]f[ée]rence|r[ée]f\.?|ref)\s*[:#]?\s*([A-Za-z0-9_\-./]{3,30})", re.I)

# DPE / GES
_DPE_RE = re.compile(r"(?:DPE|classe\s*[ée]nerg(?:ie|[ée]tique))\D{0,12}?\b([A-G])\b", re.I)
_GES_RE = re.compile(r"(?:GES|climat|[ée]mission)\D{0,12}?\b([A-G])\b", re.I)
_CONSO_RE = re.compile(r"(\d{1,4})\s*kwh\s*/?\s*m[²2]?\s*/?\s*an", re.I)
_CO2_RE = re.compile(r"(\d{1,3})\s*kg(?:\s*(?:eq)?\s*co2)?\s*/?\s*m[²2]?\s*/?\s*an", re.I)

_PRICE_M2_RE = re.compile(r"(\d[\d\s .]{2,})\s*€?\s*/\s*m[²2]", re.I)

_EXPOSURE_TOKENS = {
    "sud-ouest": "sud-ouest", "sud ouest": "sud-ouest",
    "sud-est": "sud-est", "sud est": "sud-est",
    "nord-ouest": "nord-ouest", "nord ouest": "nord-ouest",
    "nord-est": "nord-est", "nord est": "nord-est",
    "plein sud": "sud", "exposition sud": "sud", "orienté sud": "sud",
    "exposition nord": "nord", "exposition est": "est", "exposition ouest": "ouest",
}

# Équipements : motif -> attribut booléen
_EQUIPMENT_PATTERNS: dict[str, re.Pattern[str]] = {
    "elevator": re.compile(r"\bascenseur\b", re.I),
    "parking": re.compile(r"\b(?:parking|garage|stationnement|box)\b", re.I),
    "cellar": re.compile(r"\bcave\b", re.I),
    "balcony": re.compile(r"\bbalcons?\b", re.I),
    "terrace": re.compile(r"\bterrasses?\b", re.I),
    "pool": re.compile(r"\bpiscines?\b", re.I),
}

_PROPERTY_TYPE_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("maison", re.compile(r"\b(?:maison|villa|pavillon|long[èe]re|fermette)\b", re.I)),
    ("appartement", re.compile(r"\b(?:appartement|appart|studio|duplex|loft|t\d|f\d)\b", re.I)),
    ("terrain", re.compile(r"\bterrain\b", re.I)),
]


@dataclass
class ListingFeatures:
    """Caractéristiques structurées d'une annonce, indépendantes de la source."""

    # Texte / localisation
    title: str | None = None
    city: str | None = None
    postcode: str | None = None
    neighborhood: str | None = None
    partial_address: str | None = None
    latitude: float | None = None
    longitude: float | None = None

    # Caractéristiques physiques
    property_type: str | None = None
    surface: float | None = None
    land_surface: float | None = None
    rooms: int | None = None
    bedrooms: int | None = None
    floor: int | None = None
    floors_total: int | None = None
    has_elevator: bool | None = None
    has_parking: bool | None = None
    has_cellar: bool | None = None
    has_balcony: bool | None = None
    has_terrace: bool | None = None
    has_pool: bool | None = None
    construction_year: int | None = None
    exposure: str | None = None

    # DPE
    dpe_energy_class: str | None = None
    dpe_climate_class: str | None = None
    energy_consumption: int | None = None
    co2_emission: int | None = None

    # Commercial
    price: int | None = None
    price_per_m2: int | None = None
    published_at: str | None = None
    agency: str | None = None
    listing_reference: str | None = None

    # Métadonnées image (renseignées par un analyseur externe — voir image_meta.py)
    image_meta: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> "ListingFeatures":
        if not data:
            return cls()
        valid = {k: v for k, v in data.items() if k in cls.__dataclass_fields__}
        return cls(**valid)

    def filled_count(self) -> int:
        d = self.to_dict()
        d.pop("image_meta", None)
        return sum(1 for v in d.values() if v not in (None, "", []))


def _num(text: str) -> float | None:
    try:
        return float(text.replace(" ", "").replace(" ", "").replace(",", "."))
    except (TypeError, ValueError, AttributeError):
        return None


def _int_digits(text: str) -> int | None:
    digits = re.sub(r"[^\d]", "", text or "")
    return int(digits) if digits else None


def _first_group(m: re.Match[str] | None) -> str | None:
    if not m:
        return None
    for g in m.groups():
        if g:
            return g
    return None


def _extract_jsonld(soup: BeautifulSoup) -> list[dict]:
    out: list[dict] = []
    for tag in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(tag.string or "")
        except (json.JSONDecodeError, TypeError):
            continue
        if isinstance(data, list):
            out.extend(d for d in data if isinstance(d, dict))
        elif isinstance(data, dict):
            out.append(data)
            graph = data.get("@graph")
            if isinstance(graph, list):
                out.extend(g for g in graph if isinstance(g, dict))
    return out


def _coords_from_jsonld(blobs: list[dict]) -> tuple[float | None, float | None]:
    for b in blobs:
        geo = b.get("geo") if isinstance(b.get("geo"), dict) else None
        if geo:
            lat, lon = _num(str(geo.get("latitude"))), _num(str(geo.get("longitude")))
            if lat and lon:
                return lat, lon
    return None, None


def _coords_from_html(html: str) -> tuple[float | None, float | None]:
    m = re.search(r'"lat(?:itude)?"\s*:\s*(-?\d{1,2}\.\d{3,})', html, re.I)
    m2 = re.search(r'"l(?:ng|on|ongitude)"\s*:\s*(-?\d{1,3}\.\d{3,})', html, re.I)
    lat = _num(m.group(1)) if m else None
    lon = _num(m2.group(1)) if m2 else None
    if lat and lon and -5.5 <= lon <= 10 and 41 <= lat <= 52:  # France métropole approx.
        return lat, lon
    return None, None


def extract_listing_features(
    lead,
    soup: BeautifulSoup | None,
    page_url: str = "",
    html: str | None = None,
) -> ListingFeatures:
    """Construit les `ListingFeatures` depuis le lead déjà parsé + le HTML.

    On part des champs fiables déjà calculés sur le lead (prix/surface/date
    validés par consensus, ville/CP/secteur), puis on enrichit avec les
    caractéristiques fines lues dans le texte et le JSON-LD.
    """
    from crawler.extractors import get_main_content_root, _get_hero_text

    feats = ListingFeatures()
    extras = getattr(lead, "raw_extras", None) or {}

    # 1) Champs fiables hérités du lead (déjà vérifiés en amont)
    feats.title = extras.get("listing_title") or None
    feats.city = getattr(lead, "city", None)
    feats.postcode = getattr(lead, "postcode", None)
    feats.neighborhood = getattr(lead, "sector", None)
    feats.partial_address = getattr(lead, "address", None)
    feats.surface = getattr(lead, "surface", None)
    feats.price = getattr(lead, "price", None)
    feats.published_at = getattr(lead, "published_at", None)
    feats.agency = getattr(lead, "agency", None)
    feats.latitude = getattr(lead, "latitude", None)
    feats.longitude = getattr(lead, "longitude", None)

    if soup is None and html is not None:
        soup = BeautifulSoup(html, "lxml")
    if soup is None:
        return feats

    hero = _get_hero_text(get_main_content_root(soup, page_url), 6000)
    full = " ".join(p for p in (feats.title or "", hero) if p)
    low = full.lower()
    blobs = _extract_jsonld(soup)

    # 2) Coordonnées GPS
    if feats.latitude is None or feats.longitude is None:
        lat, lon = _coords_from_jsonld(blobs)
        if lat is None and html is not None:
            lat, lon = _coords_from_html(html)
        feats.latitude = feats.latitude or lat
        feats.longitude = feats.longitude or lon

    # 3) Type de bien
    for label, pat in _PROPERTY_TYPE_PATTERNS:
        if pat.search(low):
            feats.property_type = label
            break

    # 4) Surfaces
    if feats.surface is None:
        m = _SURFACE_HAB_RE.search(full)
        if m:
            feats.surface = _num(m.group(1))
    mt = _SURFACE_TERRAIN_RE.search(full)
    if mt:
        feats.land_surface = _num(_first_group(mt) or "")

    # 5) Pièces / chambres / étages
    mr = _ROOMS_RE.search(full)
    if mr:
        feats.rooms = _int_digits(_first_group(mr) or "")
    mb = _BEDROOMS_RE.search(full)
    if mb:
        feats.bedrooms = _int_digits(mb.group(1))
    mf = _FLOOR_RE.search(low)
    if mf:
        feats.floor = _int_digits(_first_group(mf) or "")
    mft = _FLOORS_TOTAL_RE.search(low)
    if mft:
        feats.floors_total = _int_digits(_first_group(mft) or "")

    # 6) Équipements (présence explicite uniquement → True ; absence ⇒ None, pas False)
    for attr, pat in _EQUIPMENT_PATTERNS.items():
        if pat.search(low):
            setattr(feats, f"has_{attr}", True)

    # 7) Année / exposition
    my = _YEAR_RE.search(low)
    if my:
        yr = _int_digits(_first_group(my) or "")
        if yr and 1700 <= yr <= 2100:
            feats.construction_year = yr
    for token, canon in _EXPOSURE_TOKENS.items():
        if token in low:
            feats.exposure = canon
            break

    # 8) DPE / GES / conso / CO2
    md = _DPE_RE.search(full)
    if md:
        feats.dpe_energy_class = md.group(1).upper()
    mg = _GES_RE.search(full)
    if mg:
        feats.dpe_climate_class = mg.group(1).upper()
    mc = _CONSO_RE.search(full)
    if mc:
        feats.energy_consumption = _int_digits(mc.group(1))
    mco2 = _CO2_RE.search(full)
    if mco2:
        feats.co2_emission = _int_digits(mco2.group(1))

    # 9) Commercial
    mp = _PRICE_M2_RE.search(full)
    if mp:
        feats.price_per_m2 = _int_digits(mp.group(1))
    elif feats.price and feats.surface:
        try:
            feats.price_per_m2 = int(feats.price / feats.surface)
        except (ZeroDivisionError, TypeError):
            pass
    mref = _REF_RE.search(full)
    if mref:
        feats.listing_reference = mref.group(1)

    # 10) Métadonnées image (si un analyseur a déjà tourné en amont)
    if isinstance(extras.get("image_meta"), dict):
        feats.image_meta = extras["image_meta"]

    return feats


def apply_features_to_lead(lead, soup: BeautifulSoup | None, page_url: str = "", html: str | None = None) -> ListingFeatures:
    """Extrait les features et les range dans `lead.raw_extras["listing_features"]`."""
    feats = extract_listing_features(lead, soup, page_url, html=html)
    try:
        lead.raw_extras = dict(getattr(lead, "raw_extras", None) or {})
        lead.raw_extras["listing_features"] = feats.to_dict()
    except Exception:
        pass
    return feats
