"""Construction d’URLs de recherche filtrées par ville."""

from __future__ import annotations

import re
from urllib.parse import quote, urlencode, urlparse, parse_qs, urlunparse

from crawler.portals import resolve_base_portal_id

_CITY_SLUG = re.compile(r"[^a-z0-9]+")

# Slugs « ville-département » pour les portails qui utilisent des chemins (pas ?ville=).
_CITY_PATH_SLUGS: dict[str, str] = {
    "paris": "paris-75",
    "marseille": "marseille-13",
    "lyon": "lyon-69",
    "toulouse": "toulouse-31",
    "nice": "nice-06",
    "nantes": "nantes-44",
    "strasbourg": "strasbourg-67",
    "montpellier": "montpellier-34",
    "bordeaux": "bordeaux-33",
    "lille": "lille-59",
}


def _slug(city: str) -> str:
    return _CITY_SLUG.sub("-", city.lower().strip()).strip("-")


def _city_path_slug(city: str) -> str:
    sl = _slug(city)
    return _CITY_PATH_SLUGS.get(sl, sl)


def apply_city_to_search_url(search_url: str, source_id: str, city: str | None) -> str:
    """Retourne l’URL de liste avec filtre ville si possible."""
    city = (city or "").strip()
    if not city:
        return search_url

    portal = resolve_base_portal_id(source_id) or (source_id or "").lower()
    slug = _slug(city)
    q_city = quote(city)

    if portal == "leboncoin":
        parsed = urlparse(search_url)
        qs = parse_qs(parsed.query)
        qs["locations"] = [city]
        qs["city"] = [city]
        new_query = urlencode({k: v[0] if len(v) == 1 else v for k, v in qs.items()}, doseq=True)
        return urlunparse(parsed._replace(query=new_query))

    if portal == "pap":
        base = search_url.rstrip("/")
        if slug:
            return f"{base}/{slug}" if "/annonce/" in base else f"{base}?ville={q_city}"
        return search_url

    if portal == "seloger":
        # list.htm?places=[…] renvoie souvent une page vide / erreur HTTP — chemin stable.
        path_slug = _city_path_slug(city)
        return f"https://www.seloger.com/immobilier/achat/bien/ile-de-france/{path_slug}/"

    if portal == "logicimmo":
        path_slug = _city_path_slug(city)
        return f"https://www.logic-immo.com/vente-immobilier/{path_slug}/liste-1"

    if portal == "bienici":
        parsed = urlparse(search_url)
        path = parsed.path.rstrip("/")
        return f"{parsed.scheme}://{parsed.netloc}{path}/{slug}" if slug else search_url

    if portal == "paruvendu":
        return f"{search_url.rstrip('/')}?ville={q_city}"

    if portal == "lefigaro" or "figaro" in search_url:
        path_slug = _city_path_slug(city).replace("-", "+")
        return f"https://immobilier.lefigaro.fr/annonces/immobilier-vente-bien-{path_slug}.html"

    parsed = urlparse(search_url)
    sep = "&" if parsed.query else "?"
    return f"{search_url}{sep}ville={q_city}&city={q_city}"


# Gabarits de chemins « ville » fréquents sur les sites immobiliers génériques.
_GENERIC_CITY_PATH_TEMPLATES = (
    "/acheter-maison-{s}",
    "/acheter-appartement-{s}",
    "/acheter-{s}",
    "/louer-maison-{s}",
    "/louer-appartement-{s}",
    "/louer-{s}",
    "/vente-maison-{s}",
    "/vente-appartement-{s}",
    "/vente-immobiliere-{s}",
    "/immobilier-{s}",
    "/immobilier/{s}",
    "/annonces/{s}",
    "/{s}",
)


def build_city_seed_urls(
    base_url: str,
    search_url: str,
    source_id: str,
    city: str | None,
) -> list[str]:
    """Points d'entrée prioritaires filtrés sur la ville (le crawl est local).

    - Portail connu : URL de recherche ville native (leboncoin, seloger, pap…).
    - Site générique : on tente les chemins « ville » courants (slug).
    """
    city = (city or "").strip()
    if not city:
        return []

    out: list[str] = []

    def _add(u: str | None) -> None:
        if u and u.startswith("http"):
            u = u.split("#")[0].rstrip("/")
            if u not in out:
                out.append(u)

    # 1) URL ville native du portail / fallback ?ville= sur l’URL configurée
    _add(apply_city_to_search_url(search_url, source_id, city))

    portal = resolve_base_portal_id(source_id)
    if portal:
        return out

    # 2) Site custom : ne pas ouvrir des dizaines de /acheter-ville (404 → pages blanches Chrome)
    return out


def preview_search_urls_for_sources(
    sources: list[dict],
    city: str | None,
    *,
    adapter_search_urls: dict[str, str] | None = None,
) -> dict[str, str]:
    """URLs de liste affichables / crawlables pour chaque source et une ville donnée."""
    city = (city or "").strip()
    adapter_search_urls = adapter_search_urls or {}
    out: dict[str, str] = {}
    for src in sources or []:
        sid = src.get("id") or ""
        if not sid:
            continue
        base = (
            adapter_search_urls.get(sid)
            or (src.get("search_url") or "").strip()
            or (src.get("base_url") or "").strip()
        )
        if not base:
            continue
        out[sid] = apply_city_to_search_url(base, sid, city) if city else base
    return out
