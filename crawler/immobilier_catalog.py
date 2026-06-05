"""Catalogue sites immobiliers : réseaux d'agences, petites annonces, moteurs agence.

Synchronisés par agence (`{agency_id}_net_{id}`) — même moteur que les portails :
rotation IP, extraction générique (m², adresse, tel, email, agence/particulier),
veille avec recrawl de toutes les fiches existantes + découverte de nouvelles.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal
from urllib.parse import quote, urlparse

from crawler.fr_communes import path_slug_for_city, slugify

SiteKind = Literal["network", "classified", "annonces"]

# Motifs d'URL fiche fréquents (réseaux + WordPress + moteurs agence / CRM)
_COMMON_LISTING_PATTERNS = [
    r"/annonce[s]?/[^/\"'\s]+-\d{4,}",
    r"/annonce[s]?/\d{5,}",
    r"/bien[s]?/[^/\"'\s]+-\d{4,}",
    r"/property/[^/\"'\s]+",
    r"/properties/[^/\"'\s]+",
    r"/listing[s]?/[^/\"'\s]+",
    r"/fiche[s]?/[^/\"'\s]*\d{4,}",
    r"/detail[s]?/[^/\"'\s]*\d{4,}",
    r"/offre[s]?/[^/\"'\s]*\d{4,}",
    r"/ref[_-]?\d{5,}",
    r"/\d{5,}(?:[/?]|$|\.html?)",
    r"staticlbi\.com/[^\"'\s]+",
    r"[a-z0-9-]+\.netty\.fr/[^\"'\s]*\d{4,}",
    r"[a-z0-9-]+\.nestenn\.com/[^\"'\s]*\d{4,}",
]


@dataclass(frozen=True)
class CatalogSite:
    id: str
    name: str
    kind: SiteKind
    base_url: str
    search_url: str
    listing_patterns: tuple[str, ...] = ()
    city_path_templates: tuple[str, ...] = ()
    enabled: bool = True


def _host_pat(host: str) -> str:
    h = host.replace("www.", "").replace(".", r"\.")
    return rf"{h}/[^\"'\s]*\d{{4,}}"


CATALOG_SITES: tuple[CatalogSite, ...] = (
    # ─── Réseaux nationaux / franchises ───
    CatalogSite(
        "laforet",
        "La Forêt",
        "network",
        "https://www.laforet.com",
        "https://www.laforet.com/achat",
        (_host_pat("laforet.com"),) + tuple(_COMMON_LISTING_PATTERNS),
        ("/achat/{slug}", "/achat/appartement/{slug}", "/achat/maison/{slug}"),
    ),
    CatalogSite(
        "guy_hoquet",
        "Guy Hoquet",
        "network",
        "https://www.guy-hoquet.com",
        "https://www.guy-hoquet.com/achat",
        (_host_pat("guy-hoquet.com"),) + tuple(_COMMON_LISTING_PATTERNS),
        ("/achat/{slug}", "/immobilier/{slug}"),
    ),
    CatalogSite(
        "clic_et_bien",
        "Clic et Bien",
        "network",
        "https://www.clicetbien.com",
        "https://www.clicetbien.com/vente",
        (_host_pat("clicetbien.com"),) + tuple(_COMMON_LISTING_PATTERNS),
        ("/vente/{slug}", "/annonces/{slug}"),
    ),
    CatalogSite(
        "century21",
        "Century 21",
        "network",
        "https://www.century21.fr",
        "https://www.century21.fr/acheter/",
        (r"century21\.fr/[^\"'\s]+/detail/\d+",) + tuple(_COMMON_LISTING_PATTERNS),
        ("/acheter/{slug}", "/annonces/{slug}"),
    ),
    CatalogSite(
        "orpi",
        "ORPI",
        "network",
        "https://www.orpi.com",
        "https://www.orpi.com/achat",
        (r"orpi\.com/[^\"'\s]+/annonce/\d+",) + tuple(_COMMON_LISTING_PATTERNS),
        ("/achat/{slug}", "/recherche?ville={slug}"),
    ),
    CatalogSite(
        "foncia",
        "Foncia",
        "network",
        "https://www.foncia.com",
        "https://www.foncia.com/achat",
        (_host_pat("foncia.com"),) + tuple(_COMMON_LISTING_PATTERNS),
        ("/achat/{slug}",),
    ),
    CatalogSite(
        "safti",
        "SAFTI",
        "network",
        "https://www.safti.fr",
        "https://www.safti.fr/achat",
        (_host_pat("safti.fr"),) + tuple(_COMMON_LISTING_PATTERNS),
        ("/achat/{slug}", "/recherche/{slug}"),
    ),
    CatalogSite(
        "iad",
        "IAD France",
        "network",
        "https://www.iadfrance.fr",
        "https://www.iadfrance.fr/annonces/achat",
        (_host_pat("iadfrance.fr"),) + tuple(_COMMON_LISTING_PATTERNS),
        ("/annonces/achat/{slug}",),
    ),
    CatalogSite(
        "stephane_plaza",
        "Stéphane Plaza Immobilier",
        "network",
        "https://www.stephaneplazaimmobilier.com",
        "https://www.stephaneplazaimmobilier.com/annonces/achat",
        (_host_pat("stephaneplazaimmobilier.com"),) + tuple(_COMMON_LISTING_PATTERNS),
        ("/annonces/achat/{slug}",),
    ),
    CatalogSite(
        "capifrance",
        "Capifrance",
        "network",
        "https://www.capifrance.fr",
        "https://www.capifrance.fr/recherche",
        (_host_pat("capifrance.fr"),) + tuple(_COMMON_LISTING_PATTERNS),
    ),
    CatalogSite(
        "optimhome",
        "Optimhome",
        "network",
        "https://www.optimhome.com",
        "https://www.optimhome.com/fr/achat",
        (_host_pat("optimhome.com"),) + tuple(_COMMON_LISTING_PATTERNS),
        ("/fr/achat/{slug}",),
    ),
    CatalogSite(
        "efficity",
        "Efficity",
        "network",
        "https://www.efficity.com",
        "https://www.efficity.com/achat",
        (_host_pat("efficity.com"),) + tuple(_COMMON_LISTING_PATTERNS),
    ),
    CatalogSite(
        "proprietes_privees",
        "Propriétés Privées",
        "network",
        "https://www.proprietes-privees.com",
        "https://www.proprietes-privees.com/annonces",
        (_host_pat("proprietes-privees.com"),) + tuple(_COMMON_LISTING_PATTERNS),
    ),
    CatalogSite(
        "era",
        "ERA Immobilier",
        "network",
        "https://www.era-immobilier.fr",
        "https://www.era-immobilier.fr/achat",
        (_host_pat("era-immobilier.fr"),) + tuple(_COMMON_LISTING_PATTERNS),
    ),
    CatalogSite(
        "human_immobilier",
        "Human Immobilier",
        "network",
        "https://www.human-immobilier.fr",
        "https://www.human-immobilier.fr/achat",
        (_host_pat("human-immobilier.fr"),) + tuple(_COMMON_LISTING_PATTERNS),
    ),
    CatalogSite(
        "bsk",
        "BSK Immobilier",
        "network",
        "https://www.bskimmobilier.com",
        "https://www.bskimmobilier.com/achat",
        (_host_pat("bskimmobilier.com"),) + tuple(_COMMON_LISTING_PATTERNS),
    ),
    CatalogSite(
        "nexity",
        "Nexity",
        "network",
        "https://www.nexity.fr",
        "https://www.nexity.fr/achat",
        (_host_pat("nexity.fr"),) + tuple(_COMMON_LISTING_PATTERNS),
        enabled=False,
    ),
    CatalogSite(
        "green_acres",
        "Green-Acres",
        "network",
        "https://www.green-acres.com",
        "https://www.green-acres.fr/immobilier",
        (_host_pat("green-acres"),) + tuple(_COMMON_LISTING_PATTERNS),
    ),
    CatalogSite(
        "immobilier_fr",
        "Immobilier-France (moteur agence)",
        "network",
        "https://www.immobilier-france.fr",
        "https://www.immobilier-france.fr/vente",
        (
            r"immobilier-france\.fr/[^\"'\s]*\d{4,}",
            r"staticlbi\.com/[^\"'\s]+",
        )
        + tuple(_COMMON_LISTING_PATTERNS),
        ("/vente/{slug}", "/vente-maison/{slug}", "/vente-appartement/{slug}"),
    ),
    CatalogSite(
        "moteurs_agence",
        "Moteurs agence (Netty, Hektor, Apimo)",
        "network",
        "https://www.immobilier-france.fr",
        "https://www.immobilier-france.fr/vente",
        (
            r"[a-z0-9-]+\.netty\.fr/[^\"'\s]*\d{4,}",
            r"hektor\.fr/[^\"'\s]*\d{4,}",
            r"apimo\.net/[^\"'\s]*\d{4,}",
            r"whise\.com/[^\"'\s]*\d{4,}",
            r"modelo\.office/[^\"'\s]*\d{4,}",
        )
        + tuple(_COMMON_LISTING_PATTERNS),
        ("/vente/{slug}", "/vente-appartement/{slug}", "/vente-maison/{slug}"),
    ),
    CatalogSite(
        "nestenn",
        "Nestenn",
        "network",
        "https://www.nestenn.com",
        "https://www.nestenn.com/acheter",
        (
            r"nestenn\.com/[^\"'\s]*\d{5,}",
            r"[a-z0-9-]+\.nestenn\.com/[^\"'\s]*\d{4,}",
        )
        + tuple(_COMMON_LISTING_PATTERNS),
        ("/acheter/{slug}", "/acheter-appartement-{slug}", "/acheter-maison-{slug}"),
    ),
    CatalogSite(
        "ladresse",
        "l'Adresse",
        "network",
        "https://www.ladresse.com",
        "https://www.ladresse.com/acheter",
        (r"ladresse\.com/[^\"'\s]*(?:annonce|bien|detail)[^\"'\s]*\d+",)
        + tuple(_COMMON_LISTING_PATTERNS),
        ("/acheter/{slug}", "/acheter/appartement/{slug}", "/acheter/maison/{slug}"),
    ),
    CatalogSite(
        "citya",
        "Citya Immobilier",
        "network",
        "https://www.citya.com",
        "https://www.citya.com/annonces/vente",
        (r"citya\.com/annonces/[^\"'\s]*\d{4,}",)
        + tuple(_COMMON_LISTING_PATTERNS),
        ("/annonces/vente/{slug}", "/annonces/vente/appartement/{slug}"),
    ),
    CatalogSite(
        "megagence",
        "megAgence",
        "network",
        "https://www.megagence.com",
        "https://www.megagence.com/acheter",
        (
            r"megagence\.com/[^\"'\s]*(?:detail|bien|offre)[^\"'\s]*\d+",
            r"megagence\.com/\d{6,}",
        )
        + tuple(_COMMON_LISTING_PATTERNS),
        ("/acheter/{slug}", "/acheter/appartement/{slug}"),
    ),
    CatalogSite(
        "drhouse_immo",
        "Dr House Immo",
        "network",
        "https://www.drhouse-immo.com",
        "https://www.drhouse-immo.com/acheter",
        (
            r"drhouse-immo\.com/[^\"'\s]*\d{5,}",
            r"drhouse-immo\.com/bien/",
        )
        + tuple(_COMMON_LISTING_PATTERNS),
        ("/acheter/{slug}", "/acheter/appartement/{slug}", "/acheter/maison/{slug}"),
    ),
    # ─── Petites annonces / particuliers ───
    CatalogSite(
        "entreparticuliers",
        "Entre Particuliers",
        "classified",
        "https://www.entreparticuliers.com",
        "https://www.entreparticuliers.com/immobilier/vente",
        (_host_pat("entreparticuliers.com"),) + tuple(_COMMON_LISTING_PATTERNS),
        ("/immobilier/vente/{slug}",),
    ),
    CatalogSite(
        "topannonces",
        "Top Annonces",
        "classified",
        "https://www.topannonces.fr",
        "https://www.topannonces.fr/Immobilier/",
        (_host_pat("topannonces.fr"),) + tuple(_COMMON_LISTING_PATTERNS),
    ),
    CatalogSite(
        "vivastreet_immo",
        "Vivastreet Immobilier",
        "classified",
        "https://www.vivastreet.com",
        "https://www.vivastreet.com/immobilier/vente",
        (r"vivastreet\.com/[^\"'\s]+/immobilier/[^\"'\s]+\d{5,}",)
        + tuple(_COMMON_LISTING_PATTERNS),
    ),
    CatalogSite(
        "immojeune",
        "Immojeune",
        "classified",
        "https://www.immojeune.com",
        "https://www.immojeune.com/location-vente",
        (_host_pat("immojeune.com"),) + tuple(_COMMON_LISTING_PATTERNS),
    ),
    CatalogSite(
        "bienveo",
        "Bien'Veo",
        "classified",
        "https://www.bienveo.fr",
        "https://www.bienveo.fr/acheter",
        (_host_pat("bienveo.fr"),) + tuple(_COMMON_LISTING_PATTERNS),
        ("/acheter/{slug}", "/louer/{slug}"),
    ),
    CatalogSite(
        "annoncesjaunes",
        "Annonces Jaunes",
        "classified",
        "https://www.annoncesjaunes.fr",
        "https://www.annoncesjaunes.fr/Immobilier/",
        (_host_pat("annoncesjaunes.fr"),) + tuple(_COMMON_LISTING_PATTERNS),
        ("/Immobilier/{slug}",),
    ),
    CatalogSite(
        "acheter_louer",
        "Acheter-Louer.fr",
        "classified",
        "https://www.acheter-louer.fr",
        "https://www.acheter-louer.fr/",
        (_host_pat("acheter-louer.fr"),) + tuple(_COMMON_LISTING_PATTERNS),
        ("/achat/{slug}", "/location/{slug}"),
    ),
    CatalogSite(
        "pro_a_part",
        "Pro à Part",
        "classified",
        "https://www.pro-a-part.com",
        "https://www.pro-a-part.com/immobilier/vente",
        (_host_pat("pro-a-part.com"),) + tuple(_COMMON_LISTING_PATTERNS),
        ("/immobilier/vente/{slug}",),
    ),
    CatalogSite(
        "achat_terrain",
        "Achat-Terrain.com",
        "classified",
        "https://www.achat-terrain.com",
        "https://www.achat-terrain.com/",
        (_host_pat("achat-terrain.com"),) + tuple(_COMMON_LISTING_PATTERNS),
    ),
    CatalogSite(
        "immoxia",
        "Immoxia",
        "classified",
        "https://www.immoxia.com",
        "https://www.immoxia.com/vente",
        (_host_pat("immoxia.com"),) + tuple(_COMMON_LISTING_PATTERNS),
        ("/vente/{slug}",),
    ),
    CatalogSite(
        "citadimmo",
        "Citadimmo",
        "classified",
        "https://www.citadimmo.com",
        "https://www.citadimmo.com/vente",
        (_host_pat("citadimmo.com"),) + tuple(_COMMON_LISTING_PATTERNS),
        ("/vente/{slug}",),
    ),
    CatalogSite(
        "refleximmo",
        "Réfleximmo",
        "classified",
        "https://www.refleximmo.com",
        "https://www.refleximmo.com/vente",
        (_host_pat("refleximmo.com"),) + tuple(_COMMON_LISTING_PATTERNS),
        ("/vente/{slug}", "/annonces/{slug}"),
    ),
    CatalogSite(
        "immovision",
        "Immovision",
        "annonces",
        "https://www.immovision.com",
        "https://www.immovision.com/vente",
        (_host_pat("immovision.com"),) + tuple(_COMMON_LISTING_PATTERNS),
    ),
    CatalogSite(
        "belles_demeures",
        "Belles Demeures",
        "network",
        "https://www.bellesdemeures.com",
        "https://www.bellesdemeures.com/achat",
        (
            r"bellesdemeures\.com/[^\"'\s]*visitonline[^\"'\s]*",
            r"visitonline_a_\d{8,}",
            r"/search/visitonline",
        )
        + tuple(_COMMON_LISTING_PATTERNS),
    ),
    CatalogSite(
        "lux_residence",
        "Lux Residence",
        "network",
        "https://www.lux-residence.com",
        "https://www.lux-residence.com/fr/vente",
        (_host_pat("lux-residence.com"),) + tuple(_COMMON_LISTING_PATTERNS),
    ),
    CatalogSite(
        "la_residence",
        "La Résidence",
        "network",
        "https://www.la-residence.fr",
        "https://www.la-residence.fr/achat",
        (_host_pat("la-residence.fr"),) + tuple(_COMMON_LISTING_PATTERNS),
    ),
    CatalogSite(
        "square_habitat",
        "Square Habitat",
        "network",
        "https://www.squarehabitat.fr",
        "https://www.squarehabitat.fr/achat",
        (_host_pat("squarehabitat.fr"),) + tuple(_COMMON_LISTING_PATTERNS),
    ),
    CatalogSite(
        "credit_agricole_immo",
        "Crédit Agricole Immobilier",
        "network",
        "https://www.ca-immobilier.fr",
        "https://www.ca-immobilier.fr/achat",
        (_host_pat("ca-immobilier.fr"),) + tuple(_COMMON_LISTING_PATTERNS),
    ),
    CatalogSite(
        "barnes",
        "Barnes",
        "network",
        "https://www.barnes-international.com",
        "https://www.barnes-international.com/fr/properties/sale",
        (_host_pat("barnes-international.com"),) + tuple(_COMMON_LISTING_PATTERNS),
    ),
    CatalogSite(
        "engelvoelkers",
        "Engel & Völkers France",
        "network",
        "https://www.engelvoelkers.com",
        "https://www.engelvoelkers.com/fr/fr/immobilier/acheter",
        (_host_pat("engelvoelkers.com"),) + tuple(_COMMON_LISTING_PATTERNS),
    ),
)

CATALOG_BY_ID = {s.id: s for s in CATALOG_SITES}
CATALOG_IDS = tuple(s.id for s in CATALOG_SITES)


def catalog_scoped_id(agency_id: str, catalog_id: str) -> str:
    return f"{agency_id}_net_{catalog_id}"


def resolve_catalog_id(source_id: str | None) -> str | None:
    """`abc_net_laforet` ou `abc123_net_century21` → `laforet`."""
    sid = (source_id or "").lower().strip()
    if not sid:
        return None
    marker = "_net_"
    if marker in sid:
        tail = sid.split(marker, 1)[-1]
        if tail in CATALOG_BY_ID:
            return tail
    if sid.startswith("net_"):
        tail = sid[4:]
        if tail in CATALOG_BY_ID:
            return tail
    return None


def catalog_site_for_id(catalog_id: str) -> CatalogSite | None:
    return CATALOG_BY_ID.get(catalog_id)


def is_catalog_source(src: dict) -> bool:
    return resolve_catalog_id(src.get("id") or "") is not None


def _slug(city: str) -> str:
    return slugify(city) or city.lower().strip().replace(" ", "-")


def catalog_city_search_candidates(
    catalog_id: str,
    search_url: str,
    city: str,
    postcode: str | None = None,
) -> list[str]:
    """URLs de liste ciblant une ville pour un site du catalogue."""
    site = CATALOG_BY_ID.get(catalog_id)
    if not site:
        return [search_url] if search_url else []

    city = (city or "").strip()
    if not city:
        return [site.search_url or site.base_url]

    slug = _slug(city)
    path_slug = path_slug_for_city(city, postcode)
    q_city = quote(city)
    out: list[str] = []

    def _add(u: str | None) -> None:
        if u and u.startswith("http") and u not in out:
            out.append(u.split("#")[0].rstrip("/") or u)

    parsed = urlparse(site.base_url)
    origin = f"{parsed.scheme}://{parsed.netloc}"

    for tpl in site.city_path_templates:
        path = tpl.format(slug=slug, path_slug=path_slug, city=q_city)
        if path.startswith("http"):
            _add(path)
        else:
            _add(f"{origin}{path}")

    base = (search_url or site.search_url or site.base_url).rstrip("/")
    for suffix in (
        f"/{slug}",
        f"/{path_slug}",
        f"/ville-{slug}",
        f"/immobilier-{path_slug}",
    ):
        _add(f"{base}{suffix}")

    sep = "&" if "?" in base else "?"
    _add(f"{base}{sep}ville={q_city}&city={q_city}&localite={q_city}")
    _add(site.search_url)
    return out or [site.search_url]


def _valid_catalog_agency_id(agency_id: str | None) -> str | None:
    """Refuse pool partagé / None (sinon id « None_net_laforet » en base)."""
    from crm.leads.shared_pool import is_shared_pool_agency_id

    if is_shared_pool_agency_id(agency_id):
        return None
    aid = str(agency_id or "").strip()
    if not aid or aid.lower() == "none":
        return None
    return aid


def purge_broken_catalog_sources(conn) -> int:
    """Supprime les sources catalogue créées par erreur (agency_id manquant)."""
    # Motifs en paramètres bindés : psycopg refuse les « % » littéraux dans le SQL.
    rows = conn.execute(
        """SELECT id FROM sources
           WHERE id LIKE ?
              OR (TRIM(COALESCE(agency_id, '')) = '' AND id LIKE ?)""",
        ("None_net_%", "%_net_%"),
    ).fetchall()
    removed = 0
    for row in rows:
        sid = row["id"]
        linked = conn.execute(
            "SELECT 1 FROM leads WHERE source_id = ? LIMIT 1",
            (sid,),
        ).fetchone()
        if linked:
            continue
        conn.execute("DELETE FROM sources WHERE id = ?", (sid,))
        removed += 1
    return removed


def sync_immobilier_catalog_for_agency(agency_id: str) -> int:
    """Ajoute / met à jour les sites catalogue pour l'agence (veille + crawl tout)."""
    from crawler.storage import get_connection, _now

    aid = _valid_catalog_agency_id(agency_id)
    if not aid:
        return 0

    now = _now()
    touched = 0
    with get_connection() as conn:
        purge_broken_catalog_sources(conn)
        for site in CATALOG_SITES:
            sid = catalog_scoped_id(aid, site.id)
            enabled = 1 if site.enabled else 0
            conn.execute(
                """INSERT INTO sources
                   (id, name, base_url, search_url, enabled, is_custom,
                    agency_id, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, 0, ?, ?, ?)
                   ON CONFLICT(id) DO UPDATE SET
                     name = excluded.name,
                     base_url = excluded.base_url,
                     search_url = excluded.search_url,
                     enabled = excluded.enabled,
                     is_custom = 0,
                     agency_id = excluded.agency_id,
                     updated_at = excluded.updated_at""",
                (
                    sid,
                    site.name,
                    site.base_url,
                    site.search_url,
                    enabled,
                    aid,
                    now,
                    now,
                ),
            )
            touched += 1
        conn.commit()
    return touched
