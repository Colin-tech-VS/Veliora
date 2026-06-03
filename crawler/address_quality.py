"""Qualité d'adresse crawl — rue réelle vs ville seule (ex. « Lorient (56100) »)."""

from __future__ import annotations

import re

from crawler.hub_detection import is_hub_listing_address

_STREET_IN_ADDRESS_RE = re.compile(
    r"\b\d{1,4}\s+(?:rue|avenue|av\.?|bd|boulevard|chemin|impasse|route|allée|place|cours|quai)\b",
    re.IGNORECASE,
)
_CITY_ONLY_PAREN_RE = re.compile(r"^[A-Za-zÀ-ÿ\s\-']+\s*\(\d{5}\)\s*$")
_LEADING_STREET_NUM_RE = re.compile(r"^\d{1,4}\s*[,]?\s*\S")


def is_city_only_address(
    address: str | None,
    city: str | None = None,
    postcode: str | None = None,
) -> bool:
    """True si l'adresse ne contient qu'une commune / CP (pas de voie)."""
    a = (address or "").strip()
    if not a or a in ("—", "-"):
        return True
    if is_hub_listing_address(a):
        return True
    if _STREET_IN_ADDRESS_RE.search(a):
        return False
    if _LEADING_STREET_NUM_RE.match(a) and "," in a:
        return False
    c = (city or "").strip()
    pc = (postcode or "").strip()
    if c and re.match(rf"^{re.escape(c)}\b", a, re.I):
        return True
    if pc and pc in a:
        return True
    if _CITY_ONLY_PAREN_RE.match(a):
        return True
    return False


def is_street_level_address(
    address: str | None,
    city: str | None = None,
    postcode: str | None = None,
) -> bool:
    from crawler.validation import _address_ok

    if not _address_ok(address):
        return False
    return not is_city_only_address(address, city, postcode)


def pick_best_address(
    fresh: str | None,
    existing: str | None,
    *,
    fresh_city: str | None = None,
    fresh_postcode: str | None = None,
    existing_city: str | None = None,
    existing_postcode: str | None = None,
) -> str | None:
    """Fusion recrawl : ne jamais remplacer une rue par une ville seule."""
    from crawler.validation import _address_ok

    f = (fresh or "").strip()
    e = (existing or "").strip()
    f_ok = _address_ok(f)
    e_ok = _address_ok(e)
    if not f_ok and not e_ok:
        return None
    if f_ok and not e_ok:
        return f if is_street_level_address(f, fresh_city, fresh_postcode) else None
    if e_ok and not f_ok:
        # Recrawl sans nouvelle rue : effacer l'ancien placeholder ville en base.
        return e if is_street_level_address(e, existing_city, existing_postcode) else None
    f_street = is_street_level_address(f, fresh_city, fresh_postcode)
    e_street = is_street_level_address(e, existing_city, existing_postcode)
    if f_street and not e_street:
        return f
    if e_street and not f_street:
        return e
    if f_street and e_street:
        return f if len(f) >= len(e) else e
    # Les deux sont ville-only : ne pas conserver « Ville (CP) » dans address
    if is_city_only_address(f, fresh_city, fresh_postcode) and is_city_only_address(
        e, existing_city, existing_postcode
    ):
        return None
    if f and is_street_level_address(f, fresh_city, fresh_postcode):
        return f
    if e and is_street_level_address(e, existing_city, existing_postcode):
        return e
    return None


def scrub_lead_address_for_storage(lead) -> None:
    """Retire adresse = ville seule ou titre d'annonce avant INSERT/UPDATE."""
    from crawler.validation import _LISTING_TITLE_ADDR_RE

    addr = (getattr(lead, "address", None) or "").strip()
    if not addr:
        lead.address = None
        return
    if _LISTING_TITLE_ADDR_RE.search(addr):
        lead.address = None
        return
    try:
        from crawler.storage import _looks_like_listing_title

        if _looks_like_listing_title(addr):
            lead.address = None
            return
    except Exception:
        pass
    if is_city_only_address(
        addr,
        getattr(lead, "city", None),
        getattr(lead, "postcode", None),
    ):
        lead.address = None
