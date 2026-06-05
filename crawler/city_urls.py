"""Construction d’URLs de recherche filtrées par ville."""

from __future__ import annotations

import json
import re
from urllib.parse import quote, urlencode, urlparse, parse_qs, urlunparse

from crawler.fr_communes import (
    department_code_from_path_slug,
    path_slug_for_city,
    resolve_commune,
    slugify,
)
from crawler.portals import resolve_base_portal_id

_CITY_SLUG = re.compile(r"[^a-z0-9]+")
_DEPT_IN_PATH = re.compile(r"-((?:\d{2,3}|2[ab]))(?:\.html)?$", re.I)


def _slug(city: str) -> str:
    return slugify(city) or _CITY_SLUG.sub("-", city.lower().strip()).strip("-")


def _city_path_slug(city: str, postcode: str | None = None) -> str:
    return path_slug_for_city(city, postcode)


def city_department_code(city: str, postcode: str | None = None) -> str | None:
    """Code département (56, 2A, 971…) pour la commune si connue."""
    return department_code_from_path_slug(_city_path_slug(city, postcode))


def search_url_targets_city(url: str, city: str) -> bool:
    """True si l’URL de liste cible explicitement la ville (pas une page nationale)."""
    city = (city or "").strip()
    if not city or not url:
        return True
    slug = _slug(city)
    path_slug = _city_path_slug(city)
    ul = url.lower()
    if slug in ul or path_slug in ul:
        return True
    if f"ville={quote(city.lower())}" in ul or f"locations=" in ul:
        return True
    return False


def listing_url_likely_in_city(url: str, city: str) -> bool:
    """Pré-filtre URL : ne rejette une fiche que si son chemin désigne EXPLICITEMENT
    une autre commune (ex. alpes-de-haute-provence-04 alors qu'on vise Nantes-44).

    Beaucoup de portails (leboncoin en tête) utilisent des URLs de fiche sans aucune
    info géographique (ex. /ventes_immobilieres/2891234567.htm). Dans ce cas il n'y a
    aucun signal pour rejeter : on laisse passer et c'est l'adresse réellement extraite
    qui tranche en aval (_lead_in_target_city). Rejeter ici revenait à jeter toutes les
    annonces leboncoin → « 0 annonce ».
    """
    city = (city or "").strip()
    if not city or not url:
        return True
    slug = _slug(city)
    path_slug = _city_path_slug(city)
    target_dept = city_department_code(city)
    path = urlparse(url).path.lower()
    if slug in path or path_slug in path:
        return True
    if f"locations" in url.lower() and slug in url.lower():
        return True

    for seg in path.split("/"):
        if not seg or len(seg) < 4:
            continue
        if slug in seg or path_slug in seg:
            return True
        m = _DEPT_IN_PATH.search(seg)
        if not m:
            continue
        dept = m.group(1).upper()
        # Conflit explicite de département → autre commune, on rejette.
        if target_dept and dept != target_dept.upper():
            return False
        if not target_dept and slug not in seg and len(seg) > 14:
            return False

    # Aucun signal géographique décisif dans l'URL : on ne rejette pas ici.
    # La page de liste est déjà filtrée ville et la vérification finale se fait
    # sur l'adresse extraite de la fiche (_lead_in_target_city).
    return True


def apply_city_to_search_url(
    search_url: str,
    source_id: str,
    city: str | None,
    postcode: str | None = None,
) -> str:
    """Retourne l’URL de liste avec filtre ville si possible."""
    city = (city or "").strip()
    if not city:
        return search_url

    portal = resolve_base_portal_id(source_id) or (source_id or "").lower()
    slug = _slug(city)
    path_slug = _city_path_slug(city, postcode)
    q_city = quote(city)

    if portal == "leboncoin":
        parsed = urlparse(search_url)
        qs = parse_qs(parsed.query)
        row = resolve_commune(city, postcode)
        cp = (row or {}).get("postcode") or (postcode or "")
        # Leboncoin filtre via "Ville_CodePostal" (ex. Nantes_44000) ; le nom seul
        # renvoie une recherche NATIONALE (d'où « le lien n'affiche pas la bonne ville »).
        qs["locations"] = [f"{city}_{cp}" if cp else city]
        qs.pop("city", None)
        new_query = urlencode({k: v[0] if len(v) == 1 else v for k, v in qs.items()}, doseq=True)
        return urlunparse(parsed._replace(query=new_query))

    if portal == "pap":
        base = search_url.rstrip("/")
        if slug:
            return f"{base}/{slug}" if "/annonce/" in base else f"{base}?ville={q_city}"
        return search_url

    if portal == "seloger":
        cands = city_search_url_candidates(search_url, source_id, city, postcode=postcode)
        return cands[0] if cands else search_url

    if portal == "logicimmo":
        cands = city_search_url_candidates(search_url, source_id, city, postcode=postcode)
        return cands[0] if cands else search_url

    if portal == "bienici":
        parsed = urlparse(search_url)
        path = parsed.path.rstrip("/")
        return f"{parsed.scheme}://{parsed.netloc}{path}/{slug}" if slug else search_url

    if portal == "paruvendu":
        return f"{search_url.rstrip('/')}?ville={q_city}"

    if portal == "lesiteimmo":
        cands = city_search_url_candidates(search_url, source_id, city, postcode=postcode)
        return cands[0] if cands else search_url

    if portal == "lefigaro" or "figaro" in search_url:
        fig_slug = path_slug.replace("-", "+")
        return f"https://immobilier.lefigaro.fr/annonces/immobilier-vente-bien-{fig_slug}.html"

    parsed = urlparse(search_url)
    sep = "&" if parsed.query else "?"
    return f"{search_url}{sep}ville={q_city}&city={q_city}"


def city_search_url_candidates(
    search_url: str,
    source_id: str,
    city: str | None,
    postcode: str | None = None,
) -> list[str]:
    """URLs de liste ville, du plus fiable au repli (testées par le moteur au crawl)."""
    city = (city or "").strip()
    if not city:
        return [search_url] if search_url else []

    from crawler.immobilier_catalog import (
        catalog_city_search_candidates,
        resolve_catalog_id,
    )

    cat_id = resolve_catalog_id(source_id)
    if cat_id:
        return catalog_city_search_candidates(cat_id, search_url, city, postcode)

    portal = resolve_base_portal_id(source_id) or (source_id or "").lower()
    slug = _slug(city)
    path_slug = _city_path_slug(city, postcode)
    q_city = quote(city)
    out: list[str] = []

    def _add(u: str | None, keep_slash: bool = False) -> None:
        if u and u.startswith("http") and u not in out:
            cleaned = u.split("#")[0]
            if not keep_slash:
                cleaned = cleaned.rstrip("/") or u
            if cleaned not in out:
                out.append(cleaned)

    if portal == "seloger":
        immo = path_slug
        _add(f"https://www.seloger.com/immobilier/achat/immo-{immo}/")
        _add(f"https://www.seloger.com/immobilier/tout/immo-{immo}/")
        places = json.dumps([{"label": city}], ensure_ascii=False, separators=(",", ":"))
        base = (
            search_url
            if "seloger.com" in (search_url or "").lower()
            else "https://www.seloger.com/list.htm"
        )
        parsed = urlparse(base)
        qs = {"types": "1", "projects": "2", "places": places}
        _add(f"{parsed.scheme}://{parsed.netloc}{parsed.path}?{urlencode(qs)}")
        return out

    if portal == "logicimmo":
        _add(f"https://www.logic-immo.com/vente-appartement/{path_slug}")
        _add(f"https://www.logic-immo.com/vente-maison/{path_slug}")
        _add(f"https://www.logic-immo.com/vente-immobilier/{path_slug}/liste-1")
        base = (search_url or "https://www.logic-immo.com/vente-appartement").rstrip("/")
        _add(f"{base}/{slug}" if slug else base)
        return out

    if portal == "leboncoin":
        _add(apply_city_to_search_url(search_url, source_id, city))
        return out

    if portal == "pap":
        base = (search_url or "https://www.pap.fr/annonce/vente-appartements").rstrip("/")
        if "/annonce/" in base and slug:
            _add(f"{base}/{slug}")
        _add(f"{base}?ville={q_city}")
        return out

    if portal == "bienici":
        parsed = urlparse(search_url or "https://www.bienici.com/recherche/achat/appartement")
        path = parsed.path.rstrip("/")
        if slug:
            _add(f"{parsed.scheme}://{parsed.netloc}{path}/{slug}")
        _add(search_url or "")
        return out or [search_url]

    if portal == "paruvendu":
        # paruvendu EXIGE le slash final : /immobilier/vente/nantes/ (sinon 404).
        if slug:
            _add(f"https://www.paruvendu.fr/immobilier/vente/{slug}/", keep_slash=True)
        _add(f"{(search_url or 'https://www.paruvendu.fr/immobilier/').rstrip('/')}?ville={q_city}")
        return out

    if portal == "lefigaro" or "figaro" in (search_url or ""):
        # Les URLs ville (immobilier-vente-bien-{ville}.html) renvoient 410 (mortes).
        # Seul le national fonctionne → on l'utilise + filtre ville en aval.
        _add("https://immobilier.lefigaro.fr/annonces/immobilier-vente-bien-france.html")
        return out

    if portal == "superimmo" and path_slug:
        _add(f"https://www.superimmo.com/achat/appartement/{path_slug}")
        _add(f"https://www.superimmo.com/achat/maison/{path_slug}")
        return out

    if portal == "avendrealouer" and slug:
        _add(f"https://www.avendrealouer.fr/vente/appartement-{slug}.html")
        _add(f"https://www.avendrealouer.fr/vente/maison-{slug}.html")
        return out

    if portal == "etreproprio":
        # Pas d'URL ville (recherche en JS) → national (filtré ville en aval).
        # Évite les /achat/appartement/{ville} qui renvoyaient 404.
        _add("https://www.etreproprio.com/annonces/vente")
        return out

    if portal == "maisonappart" and slug:
        _add(f"https://www.maison-et-appartement.fr/vente-appartement/{slug}")
        _add(f"https://www.maison-et-appartement.fr/vente-maison/{slug}")
        return out

    if portal == "ouestfranceimmo" and slug:
        # Schéma actuel : /immobilier/vente/{type}/{slug}-{dept}-{insee}/
        row = resolve_commune(city, postcode)
        if row:
            cslug = f"{row['path_slug']}-{row['code']}"  # ex. nantes-44-44109
            _add(f"https://www.ouestfrance-immo.com/immobilier/vente/appartement/{cslug}/", keep_slash=True)
            _add(f"https://www.ouestfrance-immo.com/immobilier/vente/maison/{cslug}/", keep_slash=True)
        _add(f"https://www.ouestfrance-immo.com/immobilier/vente/{slug}/", keep_slash=True)
        return out

    if portal == "lesiteimmo" and slug:
        # Schéma actuel : /acheter/{type}/{slug}-{codepostal}
        row = resolve_commune(city, postcode)
        cp = (row or {}).get("postcode") or (postcode or "")
        if cp:
            _add(f"https://www.lesiteimmo.com/acheter/appartement/{slug}-{cp}")
            _add(f"https://www.lesiteimmo.com/acheter/maison/{slug}-{cp}")
        _add(f"https://www.lesiteimmo.com/recherche")
        return out

    if portal == "notaires" and q_city:
        _add(f"https://www.immobilier.notaires.fr/fr/resultats?ville={q_city}")
        return out

    _add(apply_city_to_search_url(search_url, source_id, city, postcode))
    parsed = urlparse(search_url)
    if parsed.scheme and parsed.netloc:
        sep = "&" if parsed.query else "?"
        _add(f"{search_url}{sep}ville={q_city}&city={q_city}")
    return out or [search_url]


def pick_best_city_search_url(
    search_url: str,
    source_id: str,
    city: str | None,
    postcode: str | None = None,
) -> str:
    """Meilleure URL ville sans ouvrir de page (mode automatique)."""
    city = (city or "").strip()
    candidates = city_search_url_candidates(search_url, source_id, city, postcode=postcode)
    if city:

        def _rank(u: str) -> tuple:
            ul = u.lower()
            ps = _city_path_slug(city, postcode)
            sl = _slug(city)
            if f"immo-{ps}" in ul or ps in ul:
                return (0, u)
            if sl in ul:
                return (1, u)
            return (2, u)

        candidates = sorted(candidates, key=_rank)

    for url in candidates:
        if not city or search_url_targets_city(url, city):
            return url
    return apply_city_to_search_url(search_url, source_id, city, postcode)


def pick_working_city_search_url(
    search_url: str,
    source_id: str,
    city: str | None,
    probe,
    postcode: str | None = None,
) -> str:
    """Choisit la première URL ville locale pour laquelle `probe(url)` renvoie True."""
    city = (city or "").strip()
    candidates = city_search_url_candidates(search_url, source_id, city, postcode=postcode)
    if city:

        def _rank(u: str) -> tuple:
            ul = u.lower()
            ps = _city_path_slug(city, postcode)
            sl = _slug(city)
            if f"immo-{ps}" in ul or ps in ul:
                return (0, u)
            if sl in ul:
                return (1, u)
            return (2, u)

        candidates = sorted(candidates, key=_rank)

    for url in candidates:
        if city and not search_url_targets_city(url, city):
            continue
        try:
            if probe(url):
                return url
        except Exception:
            continue
    return apply_city_to_search_url(search_url, source_id, city, postcode)


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

    for u in city_search_url_candidates(search_url, source_id, city)[:3]:
        _add(u)

    from crawler.immobilier_catalog import resolve_catalog_id

    portal = resolve_base_portal_id(source_id)
    if portal or resolve_catalog_id(source_id):
        return out

    # Site perso : chemins ville limités (évite rafales de 404)
    parsed = urlparse(base_url or search_url)
    if parsed.scheme and parsed.netloc:
        origin = f"{parsed.scheme}://{parsed.netloc}"
        ps = _city_path_slug(city)
        sl = _slug(city)
        for tpl in (
            f"/annonces/{sl}",
            f"/vente/{sl}",
            f"/immobilier/{ps}",
            f"/{ps}",
        ):
            u = f"{origin}{tpl}"
            if u not in out:
                out.append(u)
    return out[:8]


def preview_search_urls_for_sources(
    sources: list[dict],
    city: str | None,
    *,
    adapter_search_urls: dict[str, str] | None = None,
    postcode: str | None = None,
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
        out[sid] = (
            apply_city_to_search_url(base, sid, city, postcode) if city else base
        )
    return out
