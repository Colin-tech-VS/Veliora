"""Détection des titres hub / pages liste (pas une fiche annonce)."""

from __future__ import annotations

import re

HUB_ADDRESS_RE = re.compile(
    r"(?:Achat|Location)\s+(?:Appartement|Maison|Studio|bien|Bien).{0,80}:\s*"
    r"\d[\d\s\u00a0.]*\s*annonces?\b",
    re.IGNORECASE,
)

FIGARO_LISTING_RE = re.compile(
    r"/annonces/annonce-[^/\"'\s]+|/annonces/[^/\"'\s]+-\d{7,}\.html",
    re.IGNORECASE,
)
FIGARO_HUB_RE = re.compile(
    r"/annonces/immobilier-(?:vente|location)-(?:appartement|maison|bien|studio|parking)",
    re.IGNORECASE,
)

SITE_NAV_NAME_RE = re.compile(
    r"\b(?:bourse|d[ée]cideurs|le\s+scan|[ée]co\s+sport|politique|international|"
    r"culture|soci[ée]t[ée]|figaro\s+immobilier|voir\s+l.annonce|abonn[eé]|connexion)\b",
    re.IGNORECASE,
)

LISTING_TYPE_WORDS = frozenset({
    "appartement", "maison", "studio", "loft", "villa", "terrain", "parking",
    "local", "bureau", "duplex", "triplex", "penthouse", "chambre", "immeuble",
    "propriete", "propriété", "achat", "location",
})

LISTING_TITLE_NAME_RE = re.compile(
    r"(?:"
    r"\d+(?:[.,]\d+)?\s*m(?:²|2)?"
    r"|\b(?:à|a)\s+[A-Za-zÀ-ÿ\s'-]+(?:\(\d{2,3}\))?"
    r"|\(\d{2,3}\)\s*$"
    r")",
    re.IGNORECASE,
)


def is_listing_title_name(text: str | None, second: str | None = None) -> bool:
    """Titre d'annonce ou libellé site pris pour un nom de vendeur."""
    combined = " ".join(p for p in (text, second) if p and str(p).strip()).strip()
    if not combined:
        return False
    if is_site_navigation_name(combined):
        return True
    first_token = combined.split()[0].lower().strip(".,;:")
    if first_token in LISTING_TYPE_WORDS:
        return True
    if LISTING_TITLE_NAME_RE.search(combined):
        return True
    if re.search(r"\bm2\b|\bm²\b", combined, re.I):
        return True
    if re.search(r"\d+(?:[.,]\d+)?\s*m(?:²|2)?", combined, re.I):
        return True
    return False


def parse_property_label(
    listing_title: str | None,
    address: str | None,
    *,
    surface: float | None = None,
) -> str:
    """Libellé bien pour le tableau : type · ville."""
    title = (listing_title or "").strip()
    addr = (address or "").strip()

    for source in (title, addr):
        if not source or is_hub_listing_address(source):
            continue
        m = re.match(
            r"^(appartement|maison|studio|villa|loft|duplex|terrain|local|bureau|immeuble)\b",
            source,
            re.I,
        )
        if m:
            type_word = m.group(1).capitalize()
            city_m = re.search(
                r"(?:à|a)\s+([^(]+?)(?:\s*\(\d{2,3}\))?(?:\s*:|\s*$)",
                source,
                re.I,
            )
            if city_m:
                return f"{type_word} · {city_m.group(1).strip()}"

    if addr and is_hub_listing_address(addr):
        hub_match = re.match(
            r"^(?:Achat|Location)\s+"
            r"(Appartement|Maison|Studio|Terrain|Local|Bureau|Immeuble|Parking|Bien)\s+"
            r".\s*"
            r"([^(:]+)"
            r"(?:\s*\(\d{2,3}\))?",
            addr,
            re.I,
        )
        if hub_match:
            city = hub_match.group(2).strip()
            if len(city) >= 2:
                return f"{hub_match.group(1).capitalize()} · {city}"

    m = re.search(r"F-\d{5},\s*([^(]+)", addr, re.I)
    if m:
        city = m.group(1).strip()
        type_word = "Bien"
        if title:
            tm = re.match(
                r"^(appartement|maison|studio|villa|loft|duplex|terrain|local)\b",
                title,
                re.I,
            )
            if tm:
                type_word = tm.group(1).capitalize()
        return f"{type_word} · {city}"

    if title and not is_hub_listing_address(title) and not is_listing_title_name(title):
        return title[:100]

    hub_city = re.search(r"(?:à|a)\s+([^(]+?)(?:\s*\(\d{2,3}\))?", addr, re.I)
    if hub_city and is_hub_listing_address(addr):
        return f"Bien · {hub_city.group(1).strip()}"

    if addr and not is_hub_listing_address(addr) and addr != "—":
        parts = [p.strip() for p in addr.split(",") if p.strip()]
        if parts:
            return parts[-1][:80]
    return "—"


def parse_property_detail(
    address: str | None,
    *,
    surface: float | None = None,
    city: str | None = None,
) -> str:
    """Sous-titre propriété : surface + localisation."""
    parts: list[str] = []
    if surface:
        s = int(surface) if surface == int(surface) else surface
        parts.append(f"{s} m²")
    addr = (address or "").strip()
    if addr and not is_hub_listing_address(addr) and addr != "—":
        parts.append(addr[:120])
    elif addr and is_hub_listing_address(addr):
        hub_match = re.match(
            r"^(?:Achat|Location)\s+"
            r"(?:Appartement|Maison|Studio|Terrain|Local|Bureau|Immeuble|Parking|Bien)\s+"
            r".\s*"
            r"([^(:]+)"
            r"(?:\s*\(\d{2,3}\))?",
            addr,
            re.I,
        )
        if hub_match:
            city = hub_match.group(1).strip()
            if len(city) >= 2:
                parts.append(city)
    elif city:
        parts.append(city)
    return " · ".join(parts) if parts else "—"


def is_site_navigation_name(text: str | None) -> bool:
    """Menu / en-tête de site pris par erreur pour un nom de contact."""
    if not text:
        return False
    t = text.strip()
    if not t or t == "—":
        return False
    if SITE_NAV_NAME_RE.search(t):
        return True
    if len(t) > 50 and t.count(" ") >= 5:
        if not re.search(
            r"\d{1,4}\s+(?:rue|av\.?|avenue|boulevard|bd\.?|place|all[eé]e|impasse)",
            t,
            re.I,
        ):
            return True
    return False


def is_hub_listing_address(address: str | None) -> bool:
    """Titre de page liste / catégorie — pas une adresse de bien."""
    if not address or address.strip() == "—":
        return True
    a = address.strip()
    if HUB_ADDRESS_RE.search(a):
        return True
    if re.search(r"\d[\d\s\u00a0.]*\s*annonces?\b", a, re.I):
        return True
    if re.match(
        r"^(Achat|Location)\s+(Appartement|Maison|Studio|bien|Bien|Terrain|Parking)\b",
        a,
        re.I,
    ):
        return True
    if re.search(r":\s*\d[\d\s\u00a0.]*\s*annonces?\b", a, re.I):
        return True
    return False
