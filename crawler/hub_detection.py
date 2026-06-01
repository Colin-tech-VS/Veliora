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


PROPERTY_TYPE_RE = re.compile(
    r"\b(appartements?|maisons?|studios?|villas?|lofts?|duplex|triplex|penthouse|"
    r"terrains?|locaux|local|bureaux|bureau|immeubles?|ch[aâ]teaux|ch[aâ]teau|"
    r"propri[ée]t[ée]s?|parkings?|garages?|fermes?|hangars?|chambres?|"
    r"plateaux|plateau)\b",
    re.I,
)

# Forme canonique singulier/affichage par radical détecté.
_PROPERTY_TYPE_CANON = {
    "appartement": "Appartement", "appartements": "Appartement",
    "maison": "Maison", "maisons": "Maison",
    "studio": "Studio", "studios": "Studio",
    "villa": "Villa", "villas": "Villa",
    "loft": "Loft", "lofts": "Loft",
    "duplex": "Duplex", "triplex": "Triplex", "penthouse": "Penthouse",
    "terrain": "Terrain", "terrains": "Terrain",
    "local": "Local", "locaux": "Local",
    "bureau": "Bureau", "bureaux": "Bureau",
    "immeuble": "Immeuble", "immeubles": "Immeuble",
    "château": "Château", "chateau": "Château",
    "châteaux": "Château", "chateaux": "Château",
    "propriété": "Propriété", "propriete": "Propriété",
    "propriétés": "Propriété", "proprietes": "Propriété",
    "parking": "Parking", "parkings": "Parking",
    "garage": "Garage", "garages": "Garage",
    "ferme": "Ferme", "fermes": "Ferme",
    "hangar": "Hangar", "hangars": "Hangar",
    "chambre": "Chambre", "chambres": "Chambre",
    "plateau": "Plateau", "plateaux": "Plateau",
}


def detect_property_type(*texts: str | None) -> str | None:
    """Type de bien (Appartement, Maison…) détecté dans un titre/adresse."""
    for t in texts:
        if not t:
            continue
        m = PROPERTY_TYPE_RE.search(t)
        if m:
            return _PROPERTY_TYPE_CANON.get(m.group(1).lower(), m.group(1).capitalize())
    return None


def _label_city(title: str | None, addr: str | None) -> str | None:
    """Ville pour le libellé bien (titre prioritaire, puis adresse)."""
    for source in (title, addr):
        s = (source or "").strip()
        if not s:
            continue
        m = re.search(
            r"(?:à|a)\s+([A-Za-zÀ-ÿ][A-Za-zÀ-ÿ\s'\-]{1,40}?)"
            r"(?:\s*\(\d{2,3}\))?(?:\s*[:,]|\s*$)",
            s,
            re.I,
        )
        if m:
            c = m.group(1).strip(" -'")
            if len(c) >= 2 and c.split()[0].lower() not in LISTING_TYPE_WORDS:
                return c
    a = (addr or "").strip()
    if a and is_hub_listing_address(a):
        hm = re.search(r"(?:à|a)\s+([^(:]+)", a, re.I)
        if hm:
            return hm.group(1).strip(" -'")[:40] or None
    m = re.search(r"F-\d{5},\s*([^(,]+)", a, re.I)
    if m:
        return m.group(1).strip()[:40] or None
    if a and not is_hub_listing_address(a) and a != "—":
        parts = [p.strip() for p in a.split(",") if p.strip()]
        if parts:
            last = re.sub(r"^\d{4,5}\s+", "", parts[-1]).strip()
            return last[:40] or None
    return None


def parse_property_label(
    listing_title: str | None,
    address: str | None,
    *,
    surface: float | None = None,
) -> str:
    """Libellé du bien pour les tableaux / la carte.

    Préfère le vrai titre du portail (ex. « Appartement 3 pièces 68 m² Nantes »),
    qui est le plus informatif. À défaut, synthétise « Type surface · Ville » —
    jamais la ville seule (c'était l'ancien bug : les titres riches contenant
    « m² » étaient pris pour des noms de vendeur puis remplacés par la ville).
    """
    title = (listing_title or "").strip()
    addr = (address or "").strip()

    # 1) Vrai titre d'annonce du portail. On l'utilise tel quel sauf si c'est une
    #    page liste/catégorie (hub) ou un libellé de navigation de site.
    if title and not is_hub_listing_address(title) and not is_site_navigation_name(title):
        return title[:100]

    # 2) Synthèse explicite : « Type [surface] · Ville ». Jamais la ville seule.
    type_word = detect_property_type(title, addr)
    city = _label_city(title, addr)
    head = type_word or "Bien"
    if surface:
        try:
            s_val = float(surface)
            s = int(s_val) if s_val == int(s_val) else s_val
            head = f"{head} {s} m²"
        except (TypeError, ValueError):
            pass
    if city:
        return f"{head} · {city}"
    if type_word or surface:
        return head
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
