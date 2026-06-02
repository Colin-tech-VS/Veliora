"""Connecteurs vers les sources de données publiques **légales** et ouvertes.

Toutes les sources ici sont en open data, consultables sans authentification et
dont l'usage est autorisé :

- **BAN** — Base Adresse Nationale (api-adresse.data.gouv.fr) : géocodage.
- **DPE ADEME** — Diagnostics de performance énergétique (data.ademe.fr) :
  adresses réelles avec surface, classe DPE/GES, année, type de bâtiment.
- **DVF** — Demandes de valeurs foncières (Etalab) : prix/m² du marché local
  (réutilise `crm.dvf`).
- **Cadastre** — parcelles (apicarto.ign.fr) : enrichissement parcellaire.

Conception défensive : chaque connecteur a un timeout court, log + renvoie une
liste vide en cas d'échec. **Aucune donnée n'est inventée** : on ne renvoie que
ce que les API publiques retournent réellement.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

import requests

logger = logging.getLogger(__name__)

BAN_SEARCH = "https://api-adresse.data.gouv.fr/search"
DPE_EXISTANT = (
    "https://data.ademe.fr/data-fair/api/v1/datasets/"
    "dpe-v2-logements-existants/lines"
)
DPE_NEUF = (
    "https://data.ademe.fr/data-fair/api/v1/datasets/"
    "dpe-v2-logements-neufs/lines"
)
CADASTRE_PARCELLE = "https://apicarto.ign.fr/api/cadastre/parcelle"

_TIMEOUT = 12


@dataclass
class AddressCandidate:
    """Un bien candidat issu d'une source publique, à scorer contre l'annonce."""

    address: str
    source: str  # "dpe" | "ban" | ...
    latitude: float | None = None
    longitude: float | None = None
    postcode: str | None = None
    city: str | None = None
    citycode: str | None = None
    # Attributs comparables à l'annonce (renseignés selon la source)
    surface: float | None = None
    property_type: str | None = None
    rooms: int | None = None
    floors_total: int | None = None
    construction_year: int | None = None
    dpe_energy_class: str | None = None
    dpe_climate_class: str | None = None
    energy_consumption: int | None = None
    parcel: str | None = None
    raw: dict[str, Any] = field(default_factory=dict)

    def key(self) -> str:
        """Clé de déduplication (adresse + coords arrondies)."""
        lat = round(self.latitude, 5) if self.latitude else ""
        lon = round(self.longitude, 5) if self.longitude else ""
        return f"{(self.address or '').lower().strip()}|{lat}|{lon}"


def _session() -> requests.Session:
    s = requests.Session()
    s.headers.update(
        {"User-Agent": "Veliora/1.0 (pige immobiliere; address-match; contact@veliora.local)"}
    )
    return s


def _f(value: Any) -> float | None:
    try:
        return float(str(value).replace(",", "."))
    except (TypeError, ValueError):
        return None


def _i(value: Any) -> int | None:
    f = _f(value)
    return int(f) if f is not None else None


# ── BAN : géocodage / candidats au niveau rue ─────────────────────────────
def ban_candidates(
    query: str,
    *,
    postcode: str | None = None,
    citycode: str | None = None,
    limit: int = 8,
) -> list[AddressCandidate]:
    """Candidats d'adresses BAN pour une adresse partielle / un libellé."""
    query = (query or "").strip()
    if len(query) < 3:
        return []
    params: dict[str, Any] = {"q": query[:200], "limit": max(1, min(limit, 20))}
    if postcode:
        params["postcode"] = postcode
    if citycode:
        params["citycode"] = citycode
    try:
        r = _session().get(BAN_SEARCH, params=params, timeout=_TIMEOUT)
        r.raise_for_status()
        feats = r.json().get("features") or []
    except (requests.RequestException, ValueError) as exc:
        logger.warning("BAN candidates: %s", str(exc)[:160])
        return []

    out: list[AddressCandidate] = []
    for feat in feats:
        props = feat.get("properties") or {}
        coords = (feat.get("geometry") or {}).get("coordinates") or [None, None]
        # On ne garde que les résultats précis (housenumber / street), pas les
        # municipalités entières (sinon « adresse » = ville = inutile).
        if props.get("type") not in ("housenumber", "street"):
            continue
        out.append(
            AddressCandidate(
                address=props.get("label") or query,
                source="ban",
                latitude=_f(coords[1]),
                longitude=_f(coords[0]),
                postcode=props.get("postcode") or postcode,
                city=props.get("city"),
                citycode=props.get("citycode") or citycode,
                raw={"score_ban": props.get("score"), "type": props.get("type")},
            )
        )
    return out


# ── DPE ADEME : adresses réelles avec surface + classe DPE + année ─────────
# Le dataset DPE expose des noms de champs variables selon les versions ; on lit
# défensivement plusieurs clés possibles.
_DPE_SURFACE_KEYS = ("surface_habitable_logement", "surface_habitable", "shab")
_DPE_DPE_KEYS = ("etiquette_dpe", "classe_consommation_energie", "classe_dpe")
_DPE_GES_KEYS = ("etiquette_ges", "classe_estimation_ges")
_DPE_CONSO_KEYS = ("conso_5_usages_par_m2_ep", "consommation_energie", "conso_5_usages_ep")
_DPE_YEAR_KEYS = ("annee_construction", "annee_construction_logement")
_DPE_TYPE_KEYS = ("type_batiment", "tr002_type_batiment_description")
_DPE_ADDR_KEYS = ("adresse_ban", "adresse_brut", "geo_adresse", "adresse_(ban)")
_DPE_CITY_KEYS = ("nom_commune_ban", "commune", "nom_commune_(ban)")
_DPE_PC_KEYS = ("code_postal_ban", "code_postal_brut", "code_postal_(ban)")
_DPE_LAT_KEYS = ("latitude", "geopoint_lat", "_geopoint")
_DPE_LON_KEYS = ("longitude", "geopoint_lon")
_DPE_INSEE_KEYS = ("code_insee_ban", "code_insee_commune_actualise", "insee_commune")


def _pick(row: dict, keys: tuple[str, ...]) -> Any:
    for k in keys:
        if k in row and row[k] not in (None, ""):
            return row[k]
    return None


def _dpe_geopoint(row: dict) -> tuple[float | None, float | None]:
    lat = _pick(row, _DPE_LAT_KEYS)
    lon = _pick(row, _DPE_LON_KEYS)
    if isinstance(lat, str) and "," in lat and lon is None:  # "_geopoint": "48.8,2.3"
        parts = lat.split(",")
        if len(parts) == 2:
            return _f(parts[0]), _f(parts[1])
    return _f(lat), _f(lon)


def _normalize_dpe_type(value: Any) -> str | None:
    v = str(value or "").lower()
    if "maison" in v:
        return "maison"
    if "appart" in v or "immeuble" in v:
        return "appartement"
    return None


def dpe_candidates(
    *,
    citycode: str | None = None,
    postcode: str | None = None,
    city: str | None = None,
    surface: float | None = None,
    energy_class: str | None = None,
    include_new: bool = True,
    limit: int = 60,
) -> list[AddressCandidate]:
    """Bâtiments DPE d'une commune, filtrables par surface et classe énergie.

    C'est la source la plus discriminante pour identifier une annonce :
    chaque ligne DPE = une adresse réelle avec surface, classe DPE et année.
    """
    if not (citycode or postcode or city):
        return []

    # Construit une requête Elasticsearch (param `qs`) sur les champs de localisation.
    clauses: list[str] = []
    if citycode:
        clauses.append(f'(code_insee_ban:"{citycode}" OR code_insee_commune_actualise:"{citycode}")')
    elif postcode:
        clauses.append(f'(code_postal_ban:"{postcode}" OR code_postal_brut:"{postcode}")')
    elif city:
        clauses.append(f'nom_commune_ban:"{city}"')
    qs = " AND ".join(clauses)

    params: dict[str, Any] = {"qs": qs, "size": max(1, min(limit, 200))}
    # Filtre surface ± pour réduire le volume (réseau + scoring).
    if surface:
        lo, hi = max(0, surface - 12), surface + 12
        params["qs"] += f" AND surface_habitable_logement:[{lo} TO {hi}]"
    if energy_class:
        params["qs"] += f' AND etiquette_dpe:"{energy_class.upper()}"'

    rows: list[dict] = []
    endpoints = [DPE_EXISTANT] + ([DPE_NEUF] if include_new else [])
    for url in endpoints:
        try:
            r = _session().get(url, params=params, timeout=_TIMEOUT + 6)
            r.raise_for_status()
            rows.extend(r.json().get("results") or [])
        except (requests.RequestException, ValueError) as exc:
            logger.warning("DPE ADEME (%s): %s", url.rsplit("/", 2)[-2], str(exc)[:140])
            continue

    out: list[AddressCandidate] = []
    for row in rows:
        addr = _pick(row, _DPE_ADDR_KEYS)
        if not addr:
            continue
        lat, lon = _dpe_geopoint(row)
        out.append(
            AddressCandidate(
                address=str(addr),
                source="dpe",
                latitude=lat,
                longitude=lon,
                postcode=_pick(row, _DPE_PC_KEYS) or postcode,
                city=_pick(row, _DPE_CITY_KEYS) or city,
                citycode=_pick(row, _DPE_INSEE_KEYS) or citycode,
                surface=_f(_pick(row, _DPE_SURFACE_KEYS)),
                property_type=_normalize_dpe_type(_pick(row, _DPE_TYPE_KEYS)),
                construction_year=_i(_pick(row, _DPE_YEAR_KEYS)),
                dpe_energy_class=(str(_pick(row, _DPE_DPE_KEYS) or "").upper() or None),
                dpe_climate_class=(str(_pick(row, _DPE_GES_KEYS) or "").upper() or None),
                energy_consumption=_i(_pick(row, _DPE_CONSO_KEYS)),
                raw={"dataset": "dpe-ademe"},
            )
        )
    return out


# ── Cadastre IGN : enrichissement parcellaire d'un candidat ────────────────
def cadastre_parcel(lat: float | None, lon: float | None) -> str | None:
    """Référence cadastrale (section + numéro) au point donné. Enrichissement."""
    if lat is None or lon is None:
        return None
    geom = f'{{"type":"Point","coordinates":[{lon},{lat}]}}'
    try:
        r = _session().get(
            CADASTRE_PARCELLE, params={"geom": geom}, timeout=_TIMEOUT
        )
        r.raise_for_status()
        feats = r.json().get("features") or []
    except (requests.RequestException, ValueError) as exc:
        logger.warning("Cadastre parcelle: %s", str(exc)[:140])
        return None
    if not feats:
        return None
    props = feats[0].get("properties") or {}
    section = props.get("section") or ""
    numero = props.get("numero") or ""
    return f"{section}{numero}".strip() or None
