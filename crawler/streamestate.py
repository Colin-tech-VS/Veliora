"""Intégration API StreamEstate → fiches Veliora (LeadData)."""

from __future__ import annotations

import logging
import os
import re
from datetime import datetime, timezone
from typing import Any, Iterator
import requests

from crawler.extractors import LeadData, normalize_phone, split_name

logger = logging.getLogger(__name__)

API_BASE = "https://api.stream.estate/documents/properties"

PROPERTY_TYPE_LABELS = {
    0: "Appartement",
    1: "Maison",
    2: "Immeuble",
    3: "Parking",
    4: "Bureau",
    5: "Terrain",
    6: "Commerce",
}


class StreamEstateError(Exception):
    """Erreur API StreamEstate."""


class StreamEstateCreditsError(StreamEstateError):
    """Solde API insuffisant."""


class StreamEstateNotConfiguredError(StreamEstateError):
    """Clé API absente."""


def streamestate_display_name() -> str:
    from crawler.config import STREAMESTATE_DISPLAY_NAME

    return STREAMESTATE_DISPLAY_NAME


def streamestate_api_key() -> str:
    return (os.getenv("STREAMESTATE_API_KEY") or "").strip()


def streamestate_configured() -> bool:
    return bool(streamestate_api_key())


def streamestate_settings(*, veille: bool = False) -> dict[str, Any]:
    from crawler.config import (
        STREAMESTATE_INCLUDE_IN_VEILLE,
        STREAMESTATE_ITEMS_PER_PAGE,
        STREAMESTATE_MAX_LISTINGS,
        STREAMESTATE_MAX_PAGES,
        STREAMESTATE_PARTICULIER_ONLY,
        STREAMESTATE_TRANSACTION_SALE,
        STREAMESTATE_VEILLE_MAX_LISTINGS,
        STREAMESTATE_VEILLE_MAX_PAGES,
        STREAMESTATE_WITH_COHERENT_PRICE,
    )

    if veille:
        max_pages = STREAMESTATE_VEILLE_MAX_PAGES
        max_listings = STREAMESTATE_VEILLE_MAX_LISTINGS
    else:
        max_pages = STREAMESTATE_MAX_PAGES
        max_listings = STREAMESTATE_MAX_LISTINGS

    return {
        "items_per_page": STREAMESTATE_ITEMS_PER_PAGE,
        "max_pages": max_pages,
        "max_listings": max_listings,
        "particulier_only": STREAMESTATE_PARTICULIER_ONLY,
        "transaction_sale": STREAMESTATE_TRANSACTION_SALE,
        "with_coherent_price": STREAMESTATE_WITH_COHERENT_PRICE,
        "include_in_veille": STREAMESTATE_INCLUDE_IN_VEILLE,
        "veille": veille,
    }


def _api_headers() -> dict[str, str]:
    key = streamestate_api_key()
    if not key:
        raise StreamEstateNotConfiguredError(
            f"{streamestate_display_name()} — service non configuré (contactez l'administrateur Veliora)"
        )
    return {
        "X-API-KEY": key,
        "Content-Type": "application/json",
        "Accept": "application/ld+json",
    }


def _parse_iso_date(raw: str | None) -> str | None:
    if not raw:
        return None
    try:
        dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        return dt.astimezone(timezone.utc).strftime("%Y-%m-%d")
    except ValueError:
        m = re.match(r"^(\d{4}-\d{2}-\d{2})", raw)
        return m.group(1) if m else None


def _pick_advert(property_doc: dict[str, Any]) -> dict[str, Any] | None:
    adverts = property_doc.get("adverts") or []
    candidates = [a for a in adverts if isinstance(a, dict) and (a.get("url") or a.get("contact"))]
    if not candidates:
        return None

    def _score(ad: dict[str, Any]) -> tuple[int, str]:
        pub = ad.get("publisher") or {}
        contact = ad.get("contact") or {}
        score = 0
        if pub.get("type") == 0:
            score += 20
        if contact.get("phone"):
            score += 10
        if contact.get("email"):
            score += 5
        if ad.get("url"):
            score += 3
        updated = ad.get("updatedAt") or ad.get("createdAt") or ""
        return score, updated

    return max(candidates, key=_score)


def _publisher_type(property_doc: dict[str, Any], advert: dict[str, Any] | None) -> str:
    """Type annonceur : l'advert choisi prime sur publisherTypes agrégés au niveau property."""
    pub = (advert or {}).get("publisher") or {}
    if pub.get("type") == 0:
        return "particulier"
    if pub.get("type") == 1:
        return "agence"
    agency = ((advert or {}).get("contact") or {}).get("agency") or ""
    if agency:
        return "agence"
    pub_types = property_doc.get("publisherTypes") or []
    if isinstance(pub_types, list):
        if 0 in pub_types and 1 not in pub_types:
            return "particulier"
        if 1 in pub_types:
            return "agence"
    return "particulier"


def _transaction(property_doc: dict[str, Any]) -> tuple[str, str | None]:
    tx = property_doc.get("transactionType")
    if tx == 1:
        return "location", "month"
    return "vente", None


def _parse_positive_int(raw: Any) -> int | None:
    if raw is None:
        return None
    try:
        val = int(round(float(raw)))
    except (TypeError, ValueError):
        return None
    return val if val > 0 else None


def _parse_positive_float(raw: Any) -> float | None:
    if raw is None:
        return None
    try:
        val = float(raw)
    except (TypeError, ValueError):
        return None
    return val if val > 0 else None


def _prices_close(a: int, b: int, tolerance: float = 0.15) -> bool:
    if a <= 0 or b <= 0:
        return False
    return abs(a - b) / max(a, b) <= tolerance


def _price(property_doc: dict[str, Any], advert: dict[str, Any] | None, tx: str) -> int | None:
    """Prix de l'advert retenu ; repli property uniquement si cohérent ou advert absent."""
    advert_prices: list[int] = []
    if advert:
        if tx == "vente":
            for raw in (advert.get("priceExcludingFees"), advert.get("price")):
                val = _parse_positive_int(raw)
                if val:
                    advert_prices.append(val)
        else:
            val = _parse_positive_int(advert.get("price"))
            if val:
                advert_prices.append(val)

    if advert_prices:
        advert_price = advert_prices[0]
        prop_price = _parse_positive_int(property_doc.get("price"))
        if prop_price and not _prices_close(advert_price, prop_price):
            logger.debug(
                "StreamEstate prix property %s ≠ advert %s — conserve advert",
                prop_price,
                advert_price,
            )
        return advert_price

    return _parse_positive_int(property_doc.get("price"))


def _surface(property_doc: dict[str, Any], advert: dict[str, Any] | None) -> float | None:
    """Surface de l'advert retenu en priorité (même source que le prix)."""
    for raw in ((advert or {}).get("surface"), property_doc.get("surface")):
        val = _parse_positive_float(raw)
        if val:
            return val
    return None


def _city_fields(property_doc: dict[str, Any]) -> tuple[str | None, str | None, str | None]:
    city_obj = property_doc.get("city") or {}
    if not isinstance(city_obj, dict):
        return None, None, None
    name = (city_obj.get("originalName") or city_obj.get("name") or "").strip() or None
    zipcode = (city_obj.get("zipcode") or "").strip() or None
    dept = ((city_obj.get("department") or {}).get("name") or "").strip() or None
    sector = name
    if dept and name and dept.lower() not in (name or "").lower():
        sector = f"{name} ({dept})"
    return name, zipcode, sector


def _source_url(property_doc: dict[str, Any], advert: dict[str, Any] | None) -> str:
    if advert and advert.get("url"):
        return str(advert["url"]).split("#")[0].strip()
    uuid = (property_doc.get("uuid") or "").strip()
    doc_id = (property_doc.get("@id") or "").strip()
    if doc_id.startswith("http"):
        return doc_id
    if uuid:
        return f"https://stream.estate/property/{uuid}"
    return ""


def _street_address(property_doc: dict[str, Any]) -> str | None:
    """Adresse réelle si disponible — jamais le titre de l'annonce."""
    locations = property_doc.get("locations") or []
    if not isinstance(locations, list):
        return None
    for loc in locations:
        if not isinstance(loc, dict):
            continue
        for key in ("street", "address", "name", "label"):
            street = (loc.get(key) or "").strip()
            if street and len(street) >= 5 and not street.lower().startswith("appartement"):
                return street
    return None


def _contact_fields(advert: dict[str, Any] | None) -> tuple[str | None, str | None, str | None, str | None]:
    if not advert:
        return None, None, None, None
    contact = advert.get("contact") or {}
    name = (contact.get("name") or "").strip()
    fn, ln = split_name(name) if name else (None, None)
    phone = normalize_phone(contact.get("phone") or contact.get("fax") or "")
    email = (contact.get("email") or "").strip() or None
    agency = (contact.get("agency") or "").strip() or None
    pub = advert.get("publisher") or {}
    if not agency and pub.get("name"):
        agency = str(pub.get("name")).strip()
    return fn, ln, phone or None, email


def property_to_lead(
    property_doc: dict[str, Any],
    *,
    portal_label: str | None = None,
) -> LeadData | None:
    """Convertit un PropertyDocument agrégé en fiche Veliora."""
    portal_label = portal_label or streamestate_display_name()
    advert = _pick_advert(property_doc)
    source_url = _source_url(property_doc, advert)
    if not source_url:
        return None

    tx, period = _transaction(property_doc)
    price = _price(property_doc, advert, tx)
    surface = _surface(property_doc, advert)
    city, postcode, sector = _city_fields(property_doc)
    fn, ln, phone, email = _contact_fields(advert)
    lead_type = _publisher_type(property_doc, advert)

    title = (
        (advert or {}).get("title")
        or property_doc.get("title")
        or ""
    ).strip()
    ptype = property_doc.get("propertyType")
    property_label = PROPERTY_TYPE_LABELS.get(ptype, "Bien")
    if not title:
        room = property_doc.get("room")
        parts = [property_label]
        if room:
            parts.append(f"{room} pièces")
        if surface:
            parts.append(f"{int(surface)} m²")
        title = " ".join(parts)

    address = _street_address(property_doc)

    publisher_name = ((advert or {}).get("publisher") or {}).get("name")
    original_source = (publisher_name or portal_label).strip()

    published = _parse_iso_date(
        property_doc.get("createdAt")
        or (advert or {}).get("createdAt")
        or property_doc.get("updatedAt")
    )

    pictures = (advert or {}).get("pictures") or property_doc.get("pictures") or []
    image_url = pictures[0] if pictures else None

    updated_raw = (
        property_doc.get("updatedAt")
        or (advert or {}).get("updatedAt")
        or property_doc.get("createdAt")
    )

    lead = LeadData(
        first_name=fn,
        last_name=ln,
        phone=phone,
        email=email,
        address=address,
        city=city,
        postcode=postcode,
        sector=sector,
        surface=surface,
        price=price,
        transaction_type=tx,
        price_period=period,
        published_at=published,
        source=original_source,
        source_url=source_url,
        agency=((advert or {}).get("contact") or {}).get("agency") or publisher_name,
        type=lead_type,
        raw_extras={
            "streamestate_uuid": property_doc.get("uuid"),
            "streamestate_property_type": ptype,
            "streamestate_title": title,
            "streamestate_description": (property_doc.get("description") or "")[:2000],
            "streamestate_publisher": publisher_name,
            "streamestate_portal": portal_label,
            "listing_image_url": image_url,
            "data_provider": "streamestate",
            "streamestate_updated_at": updated_raw,
            "listing_title": title,
        },
    )
    if property_doc.get("locations"):
        lead.raw_extras["streamestate_locations"] = property_doc.get("locations")

    adverts = property_doc.get("adverts") or []
    if isinstance(adverts, list) and len(adverts) > 1:
        others = [
            str(a.get("url")).split("#")[0].strip()
            for a in adverts
            if isinstance(a, dict)
            and a.get("url")
            and str(a.get("url")).split("#")[0].strip() != source_url
        ]
        if others:
            lead.raw_extras["alternate_urls"] = others[:5]

    return lead


def _updated_sort_key(lead: LeadData) -> float:
    """Timestamp négatif pour trier les annonces les plus récentes en premier."""
    raw = (lead.raw_extras or {}).get("streamestate_updated_at") or lead.published_at or ""
    if not raw:
        return 0.0
    try:
        dt = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
        return -dt.timestamp()
    except ValueError:
        return 0.0


def lead_importance_key(lead: LeadData) -> tuple:
    """Tri Veliora — particuliers avec contacts et prix cohérents en premier."""
    from crawler.validation import _price_per_m2_plausible

    has_phone = bool(lead.phone and lead.phone != "—")
    has_email = bool(lead.email and lead.email != "—")
    ratio_ok = 0 if _price_per_m2_plausible(lead) else 1
    return (
        0 if lead.type == "particulier" else 1,
        0 if has_phone or has_email else 1,
        ratio_ok,
        0 if lead.surface else 1,
        0 if lead.price else 1,
        _updated_sort_key(lead),
    )


def build_query_params(
    city: str | None = None,
    *,
    postcode: str | None = None,
    page: int = 1,
    veille: bool = False,
) -> dict[str, Any]:
    """Paramètres API optimisés Veliora (max annonces cohérentes, quota maîtrisé)."""
    cfg = streamestate_settings(veille=veille)
    params: dict[str, list[Any] | Any] = {
        "page": max(1, page),
        "itemsPerPage": min(30, max(1, int(cfg["items_per_page"]))),
        "withCoherentPrice": "true" if cfg["with_coherent_price"] else "false",
        "expired": "false",
        "order[updatedAt]": "desc",
    }
    if cfg["transaction_sale"]:
        params["transactionType"] = 0
    params["publisherTypes[]"] = [0] if cfg["particulier_only"] else [0, 1]
    params["propertyTypes[]"] = [0, 1]

    city = (city or "").strip()
    if city:
        from crawler.fr_communes import resolve_commune

        row = resolve_commune(city, postcode)
        if row and row.get("code"):
            params["includedInseeCodes[]"] = row["code"]
        elif row and row.get("postcode"):
            params["includedZipcodes[]"] = row["postcode"]
        elif postcode:
            params["includedZipcodes[]"] = postcode.strip()

    return params


def _flatten_params(params: dict[str, Any]) -> list[tuple[str, str]]:
    """Convertit les listes en paramètres répétés (publisherTypes[]=0&…)."""
    out: list[tuple[str, str]] = []
    for key, value in params.items():
        if isinstance(value, list):
            for item in value:
                k = key if key.endswith("[]") else f"{key}[]"
                out.append((k, str(item)))
        else:
            out.append((key, str(value)))
    return out


def fetch_properties_page(
    *,
    city: str | None = None,
    postcode: str | None = None,
    page: int = 1,
    veille: bool = False,
    timeout: float = 30.0,
) -> dict[str, Any]:
    """Une page de résultats StreamEstate."""
    params = build_query_params(city, postcode=postcode, page=page, veille=veille)
    resp = requests.get(
        API_BASE,
        headers=_api_headers(),
        params=_flatten_params(params),
        timeout=timeout,
    )
    try:
        data = resp.json()
    except ValueError as exc:
        raise StreamEstateError(f"Réponse JSON invalide ({resp.status_code})") from exc

    if resp.status_code == 403:
        desc = str(data.get("hydra:description") or data.get("hydra:title") or "")
        if "credit" in desc.lower() or "denied" in desc.lower():
            raise StreamEstateCreditsError(
                f"{streamestate_display_name()} — quota d'analyses atteint, réessayez plus tard"
            )
        raise StreamEstateError(desc or f"{streamestate_display_name()} — accès refusé")

    if resp.status_code >= 400:
        desc = str(data.get("hydra:description") or data.get("hydra:title") or resp.text[:200])
        raise StreamEstateError(f"HTTP {resp.status_code} — {desc}")

    return data


def iter_properties(
    *,
    city: str | None = None,
    postcode: str | None = None,
    max_pages: int | None = None,
    max_listings: int | None = None,
    veille: bool = False,
) -> Iterator[dict[str, Any]]:
    """Itère les PropertyDocument (pagination Hydra)."""
    cfg = streamestate_settings(veille=veille)
    page_limit = max_pages if max_pages is not None else int(cfg["max_pages"])
    listing_cap = max_listings if max_listings is not None else int(cfg["max_listings"])
    yielded = 0
    page = 1

    while page <= page_limit and yielded < listing_cap:
        data = fetch_properties_page(
            city=city,
            postcode=postcode,
            page=page,
            veille=veille,
        )
        members = data.get("hydra:member") or []
        if not members:
            break
        for doc in members:
            if not isinstance(doc, dict):
                continue
            yield doc
            yielded += 1
            if yielded >= listing_cap:
                return
        view = data.get("hydra:view") or {}
        if not view.get("hydra:next"):
            break
        page += 1


def count_properties(city: str | None = None) -> int:
    """Nombre total (itemsPerPage=0, plafond API 10 000)."""
    params = build_query_params(city, page=1)
    params["itemsPerPage"] = 0
    resp = requests.get(
        API_BASE,
        headers=_api_headers(),
        params=_flatten_params(params),
        timeout=25.0,
    )
    data = resp.json()
    if resp.status_code >= 400:
        return 0
    try:
        return int(data.get("hydra:totalItems") or 0)
    except (TypeError, ValueError):
        return 0


def iter_leads(
    *,
    city: str | None = None,
    postcode: str | None = None,
    max_pages: int | None = None,
    max_listings: int | None = None,
    veille: bool = False,
) -> Iterator[LeadData]:
    """PropertyDocument → LeadData triés, prêts pour save_lead()."""
    from crawler.storage import get_lead_by_source_url
    from crawler.url_utils import normalize_listing_url

    batch: list[LeadData] = []
    for doc in iter_properties(
        city=city,
        postcode=postcode,
        max_pages=max_pages,
        max_listings=max_listings,
        veille=veille,
    ):
        lead = property_to_lead(doc)
        if not lead or not lead.source_url:
            continue
        lead.source_url = normalize_listing_url(lead.source_url)
        batch.append(lead)

    existing: list[LeadData] = []
    fresh: list[LeadData] = []
    for lead in batch:
        if get_lead_by_source_url(lead.source_url, None):
            existing.append(lead)
        else:
            fresh.append(lead)
    existing.sort(key=lead_importance_key)
    fresh.sort(key=lead_importance_key)
    yield from existing
    yield from fresh


def streamestate_health() -> dict[str, Any]:
    """Statut pour /api/health."""
    cfg = streamestate_settings()
    out = {
        "configured": streamestate_configured(),
        "max_listings_per_sync": cfg["max_listings"],
        "max_pages": cfg["max_pages"],
        "items_per_page": cfg["items_per_page"],
    }
    if not streamestate_configured():
        out["status"] = "missing_key"
        return out
    out["status"] = "configured"
    out["include_in_veille"] = cfg.get("include_in_veille")
    return out
