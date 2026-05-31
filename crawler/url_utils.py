"""Normalisation des URLs pour l'ajout de sources."""

from __future__ import annotations

import re
from urllib.parse import urlparse

# Sous-domaines génériques immo — ne pas utiliser comme nom de marque
GENERIC_SUBDOMAINS = frozenset({
    "www", "immobilier", "immo", "maison", "app", "m", "mobile",
    "annonces", "vente", "achat", "location", "pro", "api", "cdn",
    "search", "recherche", "classifieds", "petites-annonces",
})

KNOWN_SITES: dict[str, dict] = {
    "paruvendu.fr": {
        "id": "paruvendu",
        "name": "ParuVendu",
        "base_url": "https://www.paruvendu.fr",
    },
    "leboncoin.fr": {"id": "leboncoin", "name": "LeBonCoin", "base_url": "https://www.leboncoin.fr"},
    "pap.fr": {"id": "pap", "name": "PAP", "base_url": "https://www.pap.fr"},
    "seloger.com": {"id": "seloger", "name": "SeLoger", "base_url": "https://www.seloger.com"},
    "logic-immo.com": {"id": "logicimmo", "name": "LogicImmo", "base_url": "https://www.logic-immo.com"},
    "bienici.com": {"id": "bienici", "name": "BienIci", "base_url": "https://www.bienici.com"},
    "lefigaro.fr": {"id": "lefigaro", "name": "Le Figaro Immobilier", "base_url": "https://immobilier.lefigaro.fr"},
    "figaro.fr": {"id": "lefigaro", "name": "Le Figaro Immobilier", "base_url": "https://immobilier.lefigaro.fr"},
}

# Clé = partie principale du domaine (avant TLD)
DOMAIN_DISPLAY_NAMES: dict[str, str] = {
    "lefigaro": "Le Figaro Immobilier",
    "figaro": "Le Figaro Immobilier",
    "leboncoin": "LeBonCoin",
    "seloger": "SeLoger",
    "logic-immo": "LogicImmo",
    "logicimmo": "LogicImmo",
    "bienici": "BienIci",
    "paruvendu": "ParuVendu",
    "pap": "PAP",
    "explorimmo": "Explorimmo",
    "luxuryestate": "LuxuryEstate",
    "avendrealouer": "AvendreAouer",
    "meilleursagents": "Meilleurs Agents",
    "ouestfrance": "OuestFrance Immo",
    "linternaute": "Linternaute Immo",
    "superimmo": "SuperImmo",
    "green-acres": "Green-Acres",
    "immoweb": "Immoweb",
    "idealista": "Idealista",
}


def normalize_site_url(url: str) -> str:
    url = (url or "").strip()
    if not url:
        raise ValueError("Lien requis")
    if not url.startswith(("http://", "https://")):
        url = "https://" + url
    parsed = urlparse(url)
    if not parsed.netloc:
        raise ValueError("Lien invalide — exemple : https://www.paruvendu.fr/immobilier/")
    return url


def registrable_domain(host: str) -> str:
    """Domaine marque (ex. immobilier.lefigaro.fr → lefigaro.fr)."""
    host = (host or "").lower().replace("www.", "")
    parts = host.split(".")
    if len(parts) < 2:
        return host
    if len(parts) >= 3 and parts[0] in GENERIC_SUBDOMAINS:
        return ".".join(parts[1:])
    if len(parts) >= 2:
        return ".".join(parts[-2:])
    return host


def display_name_from_domain(domain: str) -> str:
    """Nom lisible à partir du domaine (jamais « Immobilier » seul si c'est un sous-domaine)."""
    reg = registrable_domain(domain)
    key = reg.split(".")[0]
    if key in DOMAIN_DISPLAY_NAMES:
        return DOMAIN_DISPLAY_NAMES[key]
    if key in KNOWN_SITES:
        return KNOWN_SITES[key]["name"]
    # leboncoin → Leboncoin → styliser mots connus
    name = key.replace("-", " ")
    return " ".join(w.capitalize() for w in name.split())


def logo_url_for_domain(domain: str) -> str:
    """Favicon haute résolution via Google (domaine = marque)."""
    reg = registrable_domain(domain)
    return f"https://www.google.com/s2/favicons?domain={reg}&sz=128"


def logo_fallback_for_domain(domain: str) -> str:
    """Logo marque Clearbit en secours."""
    reg = registrable_domain(domain)
    return f"https://logo.clearbit.com/{reg}"


def parse_site_url(url: str) -> dict:
    """Découpe un lien simple en base_url + search_url (chemin conservé)."""
    url = normalize_site_url(url)
    parsed = urlparse(url)
    domain = parsed.netloc.lower().replace("www.", "")
    reg_domain = registrable_domain(domain)
    base_url = f"{parsed.scheme}://{parsed.netloc}"
    search_url = url.rstrip("/") or base_url

    known = KNOWN_SITES.get(reg_domain) or KNOWN_SITES.get(domain)
    if known:
        source_id = known["id"]
        name = known["name"]
        is_custom = False
        base_url = known.get("base_url", base_url)
    else:
        slug = re.sub(r"[^a-z0-9]+", "-", reg_domain.split(".")[0]).strip("-") or "site"
        source_id = f"custom-{slug}"
        name = display_name_from_domain(domain)
        is_custom = True

    if not known:
        base_url = f"{parsed.scheme}://{reg_domain}"

    return {
        "id": source_id,
        "name": name,
        "base_url": base_url.rstrip("/"),
        "search_url": search_url,
        "domain": reg_domain,
        "logo_url": logo_url_for_domain(domain),
        "logo_fallback": logo_fallback_for_domain(domain),
        "is_custom": is_custom,
    }
