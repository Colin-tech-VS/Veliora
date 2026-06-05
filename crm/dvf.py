"""
Comparatif DVF (Demandes de valeurs foncières) — données Etalab / DGFiP.

Source officielle : https://app.dvf.etalab.gouv.fr/
Fichiers CSV : https://files.data.gouv.fr/geo-dvf/latest/csv/

Pas d'API publique Etalab ; on interroge les CSV communaux (geo-DVF)
avec repli sur la médiane départementale pour les grandes villes sans fichier dédié.
"""

from __future__ import annotations

import csv
import io
import json
import logging
import math
import re
import statistics
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta, timezone
from typing import Any
import requests
from requests.adapters import HTTPAdapter

try:  # urllib3 est livré avec requests ; repli défensif si absent
    from urllib3.util.retry import Retry
except Exception:  # pragma: no cover
    Retry = None

logger = logging.getLogger(__name__)

# Nombre de fichiers DVF récupérés en parallèle lors des replis géographiques
# (arrondissements d'une grande ville, communes d'un département). Le coût est
# réseau (I/O), pas CPU : un pool de threads modeste réduit fortement la latence
# à froid sans surcharger files.data.gouv.fr.
DVF_FETCH_WORKERS = 8

GEO_COMMUNES = "https://geo.api.gouv.fr/communes"
GEO_SEARCH = "https://api-adresse.data.gouv.fr/search"
DVF_CSV_BASE = "https://files.data.gouv.fr/geo-dvf/latest/csv"
DVF_APP_URL = "https://app.dvf.etalab.gouv.fr/"

# Ventes DVF prises en compte (récentes = plus représentatives du marché actuel)
DVF_MAX_AGE_MONTHS = 24

# Comparables type Meilleurs Agents (filtrage + repli géographique)
DVF_MIN_COMPARABLES = 8
DVF_SURFACE_TOLERANCE = 0.30
DVF_RADIUS_URBAN_M = 500
DVF_RADIUS_EXPAND_M = 600
DVF_IQR_FACTOR = 1.5
DVF_COMPARABLES_DISPLAY_MAX = 12

# Alsace-Moselle + Mayotte : hors DVF DGFiP
DVF_EXCLUDED_DEPTS = frozenset({"57", "67", "68", "976"})

# Paris, Lyon, Marseille : DVF par arrondissement (pas de CSV au code « ville »)
MERGED_COMMUNE_ARRONDISSEMENTS: dict[str, list[str]] = {
    "75056": [f"751{i:02d}" for i in range(1, 21)],
    "69123": [f"6938{i}" for i in range(1, 10)],
    "13055": [f"132{i:02d}" for i in range(1, 17)],
}

_POSTCODE_RE = re.compile(r"\b(\d{5})\b")
_F_PREFIX_RE = re.compile(r"F-(\d{5})", re.I)
_CITY_DEPT_RE = re.compile(r"^(.+?)\s*\(\d{2,3}\)\s*$")
_ARRONDISSEMENT_CITY_RE = re.compile(
    r"(Paris|Lyon|Marseille)\s*(\d{1,2})(?:e|ème|er|ère|eme|ere)?",
    re.I,
)

_TYPE_MAP = {
    "appartement": "Appartement",
    "appart": "Appartement",
    "maison": "Maison",
    "studio": "Appartement",
    "loft": "Appartement",
    "duplex": "Appartement",
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _dept_from_insee(code: str) -> str:
    if not code or len(code) < 2:
        return ""
    if code.startswith(("97", "98")):
        return code[:3]
    if code.startswith(("2A", "2B")):
        return code[:2]
    return code[:2]


def _postcode_to_arrondissement(postcode: str | None) -> str | None:
    """Code INSEE d'arrondissement pour Paris / Lyon / Marseille."""
    if not postcode or len(postcode) != 5 or not postcode.isdigit():
        return None
    cp = int(postcode)
    if 75001 <= cp <= 75020:
        return f"751{cp - 75000:02d}"
    if 69001 <= cp <= 69009:
        return f"6938{cp - 69000}"
    if 13001 <= cp <= 13016:
        return f"132{cp - 13000:02d}"
    return None


def _normalize_city_name(city: str) -> str:
    city = (city or "").strip()
    if not city:
        return ""
    m = _CITY_DEPT_RE.match(city)
    if m:
        city = m.group(1).strip()
    m2 = _ARRONDISSEMENT_CITY_RE.search(city)
    if m2:
        return m2.group(1).title()
    return city


def parse_location_hint(address: str = "", city: str = "") -> dict[str, str | None]:
    """
    Extrait code postal, ville et arrondissement depuis les adresses crawlées
    (ex. « F-13005, Marseille 5ème (13) », « Achat Maison à Montpellier (34) »).
    """
    text = " ".join(p for p in (address, city) if p).strip()
    postcode: str | None = None
    parsed_city = _normalize_city_name(city)

    m = _F_PREFIX_RE.search(text)
    if m:
        postcode = m.group(1)
    if not postcode:
        m2 = _POSTCODE_RE.search(text)
        if m2:
            postcode = m2.group(1)

    m3 = re.search(r"F-\d{5},\s*([^(]+)", text, re.I)
    if m3:
        parsed_city = _normalize_city_name(m3.group(1))
    elif not parsed_city:
        m4 = re.search(r"[àa]\s+([A-Za-zÀ-ÿ\s'-]+?)\s*\(\d{2,3}\)", text)
        if m4:
            parsed_city = _normalize_city_name(m4.group(1))

    m5 = _ARRONDISSEMENT_CITY_RE.search(text)
    if m5:
        name = m5.group(1).title()
        n = int(m5.group(2))
        parsed_city = name
        if name == "Paris" and 1 <= n <= 20:
            postcode = postcode or f"750{n:02d}"
        elif name == "Lyon" and 1 <= n <= 9:
            postcode = postcode or f"6900{n}"
        elif name == "Marseille" and 1 <= n <= 16:
            postcode = postcode or f"130{n:02d}"

    arrondissement = _postcode_to_arrondissement(postcode)
    return {
        "postcode": postcode,
        "city": parsed_city or None,
        "arrondissement_code": arrondissement,
    }


def extract_listing_location(
    address: str | None = None,
    listing_title: str | None = None,
    city_hint: str | None = None,
) -> dict[str, str | None]:
    """
    Ville, code postal et secteur (quartier / arrondissement) depuis une annonce crawlée.
    Utilisé à l'enregistrement et pour le comparatif DVF.
    """
    addr = (address or "").strip()
    title = (listing_title or "").strip()
    loc = parse_location_hint(addr, city_hint or "")
    text = " ".join(p for p in (addr, title, city_hint) if p)
    sector: str | None = None

    from crawler.address_quality import _sector_is_commune_like, looks_like_street_in_commune_field

    m = re.search(r"F-\d{5},\s*([^(]+)", addr, re.I)
    if m:
        sector = m.group(1).strip()
        if _sector_is_commune_like(sector) and not loc.get("city"):
            loc["city"] = _normalize_city_name(sector)

    arr = _ARRONDISSEMENT_CITY_RE.search(text)
    if arr:
        sector = arr.group(0).strip()
        if not loc.get("city"):
            loc["city"] = arr.group(1).title()

    city = loc.get("city")
    if city and looks_like_street_in_commune_field(city):
        city = None
        loc["city"] = None
    if city and sector:
        city_norm = _normalize_city_name(city)
        sector_norm = sector.strip()
        if sector_norm.lower() == city_norm.lower():
            sector = city_norm
    elif city and not sector:
        sector = city
    elif sector and not city and _sector_is_commune_like(sector):
        city = _normalize_city_name(sector)

    return {
        "city": city,
        "postcode": loc.get("postcode"),
        "sector": sector,
        "arrondissement_code": loc.get("arrondissement_code"),
    }


def apply_lead_location_fields(lead) -> None:
    """Remplit city / postcode / sector sur un LeadData avant persistance."""
    loc = extract_listing_location(
        getattr(lead, "address", None),
        (getattr(lead, "raw_extras", None) or {}).get("listing_title"),
        getattr(lead, "city", None),
    )
    if loc.get("city"):
        lead.city = loc["city"]
    if loc.get("postcode"):
        lead.postcode = loc["postcode"]
    if loc.get("sector"):
        lead.sector = loc["sector"]


def _dvf_commune_candidates(
    code_commune: str,
    *,
    postcode: str | None = None,
    arrondissement_code: str | None = None,
) -> list[str]:
    """Ordre de recherche des CSV DVF (arrondissement → ville éclatée → commune)."""
    seen: set[str] = set()
    out: list[str] = []

    def add(code: str | None) -> None:
        if code and code not in seen:
            seen.add(code)
            out.append(code)

    add(arrondissement_code or _postcode_to_arrondissement(postcode))
    add(code_commune)
    for alt in MERGED_COMMUNE_ARRONDISSEMENTS.get(code_commune, ()):
        add(alt)
    if arrondissement_code and arrondissement_code.startswith(("751", "6938", "132")):
        for merged, alts in MERGED_COMMUNE_ARRONDISSEMENTS.items():
            if arrondissement_code in alts:
                add(merged)
                break
    return out


def _guess_type_local(surface: float | None, address: str = "") -> str:
    addr = (address or "").lower()
    if "maison" in addr or "villa" in addr or "pavillon" in addr:
        return "Maison"
    if surface and surface >= 90:
        return "Maison"
    return "Appartement"


_SESSION: requests.Session | None = None
_SESSION_LOCK = threading.Lock()


def _build_session() -> requests.Session:
    s = requests.Session()
    s.headers.update({"User-Agent": "Veliora/1.0 (pige immobiliere; contact@veliora.local)"})
    # Pool de connexions partagé (keep-alive) : évite un handshake TCP/TLS par
    # fichier DVF. Dimensionné pour les replis parallèles + l'enrichissement DVF
    # pendant le crawl (plusieurs threads partagent la même session).
    pool = max(DVF_FETCH_WORKERS * 2, 16)
    if Retry is not None:
        retry = Retry(
            total=2,
            connect=2,
            read=2,
            backoff_factor=0.4,
            status_forcelist=(429, 500, 502, 503, 504),
            allowed_methods=frozenset({"GET"}),
            raise_on_status=False,
        )
        adapter = HTTPAdapter(max_retries=retry, pool_connections=pool, pool_maxsize=pool)
    else:  # pragma: no cover
        adapter = HTTPAdapter(pool_connections=pool, pool_maxsize=pool)
    s.mount("https://", adapter)
    s.mount("http://", adapter)
    return s


def _session() -> requests.Session:
    """Session HTTP persistante (keep-alive + pool + retry). Thread-safe."""
    global _SESSION
    if _SESSION is None:
        with _SESSION_LOCK:
            if _SESSION is None:
                _SESSION = _build_session()
    return _SESSION


def _haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    r = 6_371_000
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return r * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def _geocode_from_features(feats: list, loc: dict) -> dict | None:
    if not feats:
        return None
    props = feats[0].get("properties") or {}
    citycode = props.get("citycode") or loc.get("arrondissement_code")
    return {
        "label": props.get("label"),
        "citycode": citycode,
        "postcode": props.get("postcode") or loc.get("postcode"),
        "city": props.get("city") or loc.get("city"),
        "lon": (feats[0].get("geometry") or {}).get("coordinates", [None, None])[0],
        "lat": (feats[0].get("geometry") or {}).get("coordinates", [None, None])[1],
    }


def _geocode_fallback_loc(loc: dict, label: str = "") -> dict | None:
    if not loc.get("arrondissement_code"):
        return None
    return {
        "label": label,
        "citycode": loc["arrondissement_code"],
        "postcode": loc["postcode"],
        "city": loc["city"],
        "lon": None,
        "lat": None,
    }


# Caches mémoire « succès uniquement » : un échec réseau transitoire (503, timeout)
# ne doit jamais être mémorisé, sinon toutes les estimations suivantes pour cette
# adresse repartiraient sur un repli dégradé pendant toute la vie du process.
_GEOCODE_CACHE: dict[tuple, dict] = {}
_COMMUNE_CACHE: dict[tuple, dict] = {}
_GEO_CACHE_LOCK = threading.Lock()
_GEO_CACHE_MAX = 4096


def _cache_get(cache: dict, key: tuple) -> dict | None:
    with _GEO_CACHE_LOCK:
        hit = cache.get(key)
        return dict(hit) if hit else None


def _cache_put(cache: dict, key: tuple, value: dict | None) -> None:
    if not value:
        return
    with _GEO_CACHE_LOCK:
        if len(cache) >= _GEO_CACHE_MAX:
            cache.clear()
        cache[key] = value


def geocode_address(address: str, city: str = "", *, prefer_street: bool = False) -> dict | None:
    """Géocode une adresse (API Adresse.data.gouv.fr), succès mémoïsés.

    Si prefer_street=True, tente d'abord l'adresse complète (rue + ville) pour
    affiner le rayon de comparables DVF.
    """
    key = ((address or "").strip(), (city or "").strip(), prefer_street)
    cached = _cache_get(_GEOCODE_CACHE, key)
    if cached is not None:
        return cached
    res = _geocode_address_impl(key[0], key[1], prefer_street)
    _cache_put(_GEOCODE_CACHE, key, res)
    return dict(res) if res else None


def _geocode_address_impl(address: str, city: str, prefer_street: bool) -> dict | None:
    loc = parse_location_hint(address, city)
    addr_clean = (address or "").strip()
    if addr_clean in ("—", "-", ""):
        addr_clean = ""
    street_q = ", ".join(p for p in (addr_clean, (city or "").strip()) if p).strip()
    queries: list[str] = []
    if prefer_street and street_q and len(street_q) >= 8:
        queries.append(street_q)
    if loc["postcode"] and loc["city"]:
        queries.append(f"{loc['postcode']} {loc['city']}")
    elif loc["city"]:
        queries.append(loc["city"])
    elif loc["postcode"]:
        queries.append(loc["postcode"])
    elif street_q:
        queries.append(street_q)
    seen_q: set[str] = set()
    for q in queries:
        q = q.strip()
        if not q or len(q) < 3 or q in seen_q:
            continue
        seen_q.add(q)
        try:
            r = _session().get(GEO_SEARCH, params={"q": q, "limit": 1}, timeout=12)
            r.raise_for_status()
            hit = _geocode_from_features(r.json().get("features") or [], loc)
            if hit and (hit.get("lat") is not None or not prefer_street):
                return hit
        except requests.RequestException as exc:
            logger.warning("Geocoding failed (%s): %s", q[:40], exc)
    return _geocode_fallback_loc(loc, street_q or queries[0] if queries else "")


def resolve_commune_code(city: str, postcode: str | None = None) -> dict | None:
    """Résout le code INSEE via geo.api.gouv.fr (succès mémoïsés)."""
    key = ((city or "").strip(), (postcode or "").strip() or None)
    cached = _cache_get(_COMMUNE_CACHE, key)
    if cached is not None:
        return cached
    res = _resolve_commune_code_impl(key[0], key[1])
    _cache_put(_COMMUNE_CACHE, key, res)
    return dict(res) if res else None


def _resolve_commune_code_impl(city: str, postcode: str | None) -> dict | None:
    city = _normalize_city_name(city)
    arrondissement = _postcode_to_arrondissement(postcode)
    if arrondissement:
        return {
            "code": arrondissement,
            "nom": city or postcode,
            "codeDepartement": _dept_from_insee(arrondissement),
        }
    if not city and not postcode:
        return None
    params: dict[str, Any] = {"fields": "nom,code,codeDepartement,codesPostaux", "limit": 5}
    if postcode:
        params["codePostal"] = postcode
    if city:
        params["nom"] = city
        params["boost"] = "population"
    try:
        r = _session().get(GEO_COMMUNES, params=params, timeout=12)
        r.raise_for_status()
        rows = r.json()
        if not rows:
            return None
        if city:
            city_l = city.lower()
            for row in rows:
                if row.get("nom", "").lower() == city_l:
                    return row
        return rows[0]
    except requests.RequestException as exc:
        logger.warning("Commune lookup failed: %s", exc)
        return None


def _format_sale_address(row: dict) -> str:
    num = (row.get("adresse_numero") or "").strip()
    suffix = (row.get("adresse_suffixe") or "").strip()
    voie = (row.get("adresse_nom_voie") or "").strip()
    parts = [p for p in (f"{num}{suffix}".strip(), voie) if p]
    return " ".join(parts) if parts else voie or "—"


def _parse_dvf_sale_row(
    row: dict,
    *,
    code_commune: str | None = None,
    type_local: str | None = None,
    cutoff: date,
    years: set[int],
) -> dict | None:
    if row.get("nature_mutation") != "Vente":
        return None
    tlocal = row.get("type_local") or ""
    if tlocal not in ("Appartement", "Maison"):
        return None
    if type_local and tlocal != type_local:
        return None
    if code_commune and row.get("code_commune") != code_commune:
        return None
    dm = (row.get("date_mutation") or "").strip()
    sale_date = dm[:10] if dm else None
    if sale_date:
        try:
            d = date.fromisoformat(sale_date)
            if d < cutoff:
                return None
            years.add(d.year)
        except ValueError:
            sale_date = None
    try:
        vf = float(row.get("valeur_fonciere") or 0)
        surf = float(row.get("surface_reelle_bati") or 0)
        lat = float(row.get("latitude") or 0)
        lon = float(row.get("longitude") or 0)
    except (TypeError, ValueError):
        return None
    if vf < 10_000 or surf < 9:
        return None
    pm2 = vf / surf
    if pm2 < 500 or pm2 > 25_000:
        return None
    lat_ok = -90 < lat < 90 and abs(lat) > 0.01
    lon_ok = -180 < lon < 180 and abs(lon) > 0.01
    return {
        "price_m2": round(pm2, 2),
        "price": int(round(vf)),
        "surface": round(surf, 1),
        "postcode": (row.get("code_postal") or "").strip(),
        "lat": lat if lat_ok else None,
        "lon": lon if lon_ok else None,
        "date": sale_date,
        "address": _format_sale_address(row),
        "type_local": tlocal,
    }


def _sales_from_csv_rows(
    raw: str,
    *,
    code_commune: str | None = None,
    type_local: str | None = None,
    max_age_months: int = DVF_MAX_AGE_MONTHS,
) -> tuple[list[dict], list[int]]:
    sales: list[dict] = []
    years: set[int] = set()
    cutoff = date.today() - timedelta(days=30 * max(1, max_age_months))
    reader = csv.DictReader(io.StringIO(raw))
    for row in reader:
        sale = _parse_dvf_sale_row(
            row,
            code_commune=code_commune,
            type_local=type_local,
            cutoff=cutoff,
            years=years,
        )
        if sale:
            sales.append(sale)
    return sales, sorted(years)


def _prices_from_csv_rows(
    raw: str,
    *,
    code_commune: str | None = None,
    type_local: str | None = None,
    max_age_months: int = DVF_MAX_AGE_MONTHS,
) -> tuple[list[float], list[int], list[str]]:
    sales, years = _sales_from_csv_rows(
        raw,
        code_commune=code_commune,
        type_local=type_local,
        max_age_months=max_age_months,
    )
    return [s["price_m2"] for s in sales], years, [s["postcode"] for s in sales]


def _surface_in_band(surface: float, subject: float, tolerance: float = DVF_SURFACE_TOLERANCE) -> bool:
    if subject <= 0 or surface <= 0:
        return True
    lo = subject * (1 - tolerance)
    hi = subject * (1 + tolerance)
    return lo <= surface <= hi


def _surface_band_label(subject: float, tolerance: float = DVF_SURFACE_TOLERANCE) -> str:
    lo = max(9, int(subject * (1 - tolerance)))
    hi = int(subject * (1 + tolerance))
    return f"{lo}–{hi} m²"


def _sale_distance_m(sale: dict, lat: float, lon: float) -> float | None:
    slat, slon = sale.get("lat"), sale.get("lon")
    if slat is None or slon is None:
        return None
    return _haversine_m(lat, lon, slat, slon)


def _iqr_filter_sales(sales: list[dict]) -> list[dict]:
    if len(sales) < 4:
        return sales
    vals = sorted(s["price_m2"] for s in sales)
    q1 = statistics.quantiles(vals, n=4)[0]
    q3 = statistics.quantiles(vals, n=4)[2]
    iqr = q3 - q1
    lo = q1 - DVF_IQR_FACTOR * iqr
    hi = q3 + DVF_IQR_FACTOR * iqr
    return [s for s in sales if lo <= s["price_m2"] <= hi]


def _filter_sales_pool(
    sales: list[dict],
    *,
    subject_surface: float | None = None,
    subject_lat: float | None = None,
    subject_lon: float | None = None,
    postcode: str | None = None,
    radius_m: int | None = None,
) -> list[dict]:
    pool = sales
    if subject_surface and subject_surface > 0:
        pool = [s for s in pool if _surface_in_band(s["surface"], subject_surface)]
    if radius_m and subject_lat is not None and subject_lon is not None:
        pool = [
            s
            for s in pool
            if _sale_distance_m(s, subject_lat, subject_lon) is not None
            and _sale_distance_m(s, subject_lat, subject_lon) <= radius_m
        ]
    elif postcode:
        pc = postcode.strip()
        if len(pc) == 5 and pc.isdigit():
            pool = [s for s in pool if s.get("postcode") == pc]
    return _iqr_filter_sales(pool)


def _pick_comparable_pool(
    sales: list[dict],
    *,
    subject_surface: float | None = None,
    subject_lat: float | None = None,
    subject_lon: float | None = None,
    postcode: str | None = None,
) -> tuple[list[dict], str, str]:
    """Sélectionne les ventes comparables (repli rayon → CP → commune)."""
    if subject_lat is not None and subject_lon is not None:
        for radius in (DVF_RADIUS_URBAN_M, DVF_RADIUS_EXPAND_M):
            pool = _filter_sales_pool(
                sales,
                subject_surface=subject_surface,
                subject_lat=subject_lat,
                subject_lon=subject_lon,
                radius_m=radius,
            )
            if len(pool) >= DVF_MIN_COMPARABLES:
                return pool, f"radius_{radius}m", f"ventes à ≤{radius} m (surface ±{int(DVF_SURFACE_TOLERANCE * 100)} %)"

    norm_pc = (postcode or "").strip()
    if len(norm_pc) == 5 and norm_pc.isdigit():
        pool = _filter_sales_pool(
            sales,
            subject_surface=subject_surface,
            postcode=norm_pc,
        )
        if len(pool) >= DVF_MIN_COMPARABLES:
            return pool, "postcode", f"ventes au CP {norm_pc} (surface ±{int(DVF_SURFACE_TOLERANCE * 100)} %)"

    if subject_surface and subject_surface > 0:
        pool = _filter_sales_pool(sales, subject_surface=subject_surface)
        if len(pool) >= DVF_MIN_COMPARABLES:
            return pool, "commune_surface", f"commune, surface ±{int(DVF_SURFACE_TOLERANCE * 100)} %"

    pool = _iqr_filter_sales(sales)
    return pool, "commune", "commune (toutes surfaces récentes)"


def _comparables_for_display(
    pool: list[dict],
    *,
    subject_lat: float | None = None,
    subject_lon: float | None = None,
    limit: int = DVF_COMPARABLES_DISPLAY_MAX,
) -> list[dict]:
    enriched: list[dict] = []
    for s in pool:
        dist = None
        if subject_lat is not None and subject_lon is not None:
            dist = _sale_distance_m(s, subject_lat, subject_lon)
        enriched.append({**s, "_dist": dist if dist is not None else 999_999})
    enriched.sort(key=lambda x: (x["_dist"], x.get("date") or ""), reverse=False)
    out: list[dict] = []
    for s in enriched[:limit]:
        item = {
            "date": s.get("date"),
            "price": s.get("price"),
            "surface": s.get("surface"),
            "price_m2": s.get("price_m2"),
            "address": s.get("address"),
            "postcode": s.get("postcode"),
        }
        if s.get("_dist", 999_999) < 900_000:
            item["distance_m"] = int(round(s["_dist"]))
        out.append(item)
    return out


def _stats_from_sales(
    pool: list[dict],
    *,
    geo_level: str,
    filter_detail: str,
    source_detail: str,
    stats_code: str,
    type_local: str,
    norm_postcode: str,
    sale_years: list[int],
    subject_surface: float | None = None,
    subject_lat: float | None = None,
    subject_lon: float | None = None,
) -> dict:
    pm2_vals = [s["price_m2"] for s in pool]
    median = statistics.median(pm2_vals)
    surface_band = _surface_band_label(subject_surface) if subject_surface and subject_surface > 0 else None
    return {
        "available": True,
        "code_commune": stats_code,
        "type_local": type_local,
        "median_m2": round(median, 0),
        "mean_m2": round(statistics.mean(pm2_vals), 0),
        "sample_count": len(pool),
        "geo_level": geo_level,
        "filter_detail": filter_detail,
        "surface_band": surface_band,
        "postcode": norm_postcode or None,
        "reference_period": _format_dvf_period(sale_years),
        "reference_months": DVF_MAX_AGE_MONTHS,
        "source": "etalab_geo_dvf",
        "source_detail": source_detail,
        "dvf_app_url": DVF_APP_URL,
        "updated_at": _now(),
        "comparables": _comparables_for_display(
            pool,
            subject_lat=subject_lat,
            subject_lon=subject_lon,
        ),
        "comparables_total": len(pool),
    }


def _compact_sales_for_cache(sales: list[dict], limit: int = 800) -> list[dict]:
    return [
        {
            "price_m2": s["price_m2"],
            "price": s["price"],
            "surface": s["surface"],
            "postcode": s.get("postcode"),
            "lat": s.get("lat"),
            "lon": s.get("lon"),
            "date": s.get("date"),
            "address": s.get("address"),
            "type_local": s.get("type_local"),
        }
        for s in sales[:limit]
    ]


def _format_dvf_period(years: list[int]) -> str | None:
    if not years:
        return None
    if len(years) == 1:
        return str(years[0])
    return f"{years[0]}–{years[-1]}"


def _parse_dvf_csv(text: str, code_commune: str | None = None) -> list[float]:
    """Extrait les prix/m² des ventes (filtres anti-aberrations)."""
    prices: list[float] = []
    reader = csv.DictReader(io.StringIO(text))
    for row in reader:
        if row.get("nature_mutation") != "Vente":
            continue
        tlocal = row.get("type_local") or ""
        if tlocal not in ("Appartement", "Maison"):
            continue
        if code_commune and row.get("code_commune") != code_commune:
            continue
        try:
            vf = float(row.get("valeur_fonciere") or 0)
            surf = float(row.get("surface_reelle_bati") or 0)
        except (TypeError, ValueError):
            continue
        if vf < 10_000 or surf < 9:
            continue
        pm2 = vf / surf
        if pm2 < 500 or pm2 > 25_000:
            continue
        prices.append(pm2)
    return prices


def _fetch_commune_csv(code_commune: str, year: str) -> str | None:
    dept = _dept_from_insee(code_commune)
    if dept in DVF_EXCLUDED_DEPTS:
        return None
    url = f"{DVF_CSV_BASE}/{year}/communes/{dept}/{code_commune}.csv"
    try:
        r = _session().get(url, timeout=45)
        if r.status_code == 404:
            return None
        r.raise_for_status()
        return r.text
    except requests.RequestException as exc:
        logger.warning("DVF CSV %s: %s", url, exc)
        return None


def _fetch_commune_csv_any(code_commune: str, years: tuple[str, ...]) -> str | None:
    """Premier CSV non vide parmi plusieurs millésimes (ordre = priorité)."""
    for year in years:
        raw = _fetch_commune_csv(code_commune, year)
        if raw:
            return raw
    return None


def _iter_commune_csv_parallel(codes: list[str], years: tuple[str, ...]):
    """Récupère les CSV DVF de plusieurs communes en parallèle, par lots.

    Yield (code_commune, csv_text) dès qu'un lot est prêt — le consommateur peut
    s'arrêter tôt (assez de ventes) sans avoir lancé tous les téléchargements.
    """
    if not codes:
        return
    if len(codes) == 1:
        raw = _fetch_commune_csv_any(codes[0], years)
        if raw:
            yield codes[0], raw
        return
    workers = min(DVF_FETCH_WORKERS, len(codes))
    with ThreadPoolExecutor(max_workers=workers) as pool:
        for start in range(0, len(codes), workers):
            batch = codes[start : start + workers]
            for cc, raw in zip(batch, pool.map(lambda c: _fetch_commune_csv_any(c, years), batch)):
                if raw:
                    yield cc, raw


def _fetch_dept_commune_codes(dept: str, limit: int = 80) -> list[str]:
    """Codes INSEE d'un département ; inclut les arrondissements Paris/Lyon/Marseille."""
    codes: list[str] = []
    if dept == "75":
        codes.extend(MERGED_COMMUNE_ARRONDISSEMENTS["75056"])
    elif dept == "69":
        codes.extend(MERGED_COMMUNE_ARRONDISSEMENTS["69123"])
    elif dept == "13":
        codes.extend(MERGED_COMMUNE_ARRONDISSEMENTS["13055"])
    try:
        r = _session().get(
            GEO_COMMUNES,
            params={"codeDepartement": dept, "fields": "code", "limit": limit},
            timeout=15,
        )
        r.raise_for_status()
        for row in r.json():
            code = row.get("code")
            if code and code not in codes:
                codes.append(code)
    except requests.RequestException:
        pass
    return codes[: max(limit, len(codes))]


def _load_cached_stats(code_commune: str, type_local: str) -> dict | None:
    from crawler.storage import get_connection

    key = f"{code_commune}:{type_local}"
    with get_connection() as conn:
        row = conn.execute(
            "SELECT payload FROM dvf_commune_cache WHERE cache_key = ?", (key,)
        ).fetchone()
        if not row:
            return None
        try:
            data = json.loads(row["payload"])
            return data if data.get("available") else None
        except json.JSONDecodeError:
            return None


def _save_cached_stats(code_commune: str, type_local: str, stats: dict) -> None:
    from crawler.storage import get_connection, _now as storage_now

    if not stats.get("available"):
        return
    key = f"{code_commune}:{type_local}"
    with get_connection() as conn:
        conn.execute(
            """INSERT OR REPLACE INTO dvf_commune_cache (cache_key, payload, updated_at)
               VALUES (?, ?, ?)""",
            (key, json.dumps(stats), storage_now()),
        )
        conn.commit()


def compute_price_stats(
    code_commune: str,
    type_local: str = "Appartement",
    *,
    filter_type: bool = True,
    postcode: str | None = None,
    arrondissement_code: str | None = None,
    subject_surface: float | None = None,
    subject_lat: float | None = None,
    subject_lon: float | None = None,
) -> dict | None:
    """
    Médiane DVF et comparables filtrés (type, surface ±30 %, IQR, rayon/CP).
    Repli : arrondissements (Paris/Lyon/Marseille) puis médiane départementale.
    """
    if not code_commune:
        return None

    base_cache = arrondissement_code or _postcode_to_arrondissement(postcode) or code_commune
    norm_postcode = (postcode or "").strip()
    norm_postcode = norm_postcode if (len(norm_postcode) == 5 and norm_postcode.isdigit()) else ""
    cache_key = f"{base_cache}|cp{norm_postcode}" if norm_postcode else base_cache
    cached = _load_cached_stats(cache_key, type_local)
    sales: list[dict] | None = None
    sale_years: list[int] = []
    source_detail = "commune"
    stats_code = base_cache

    if cached and cached.get("raw_sales"):
        sales = cached["raw_sales"]
        sale_years = cached.get("sale_years") or []
        source_detail = cached.get("source_detail") or source_detail
        stats_code = cached.get("code_commune") or stats_code

    dept = _dept_from_insee(code_commune)
    if dept in DVF_EXCLUDED_DEPTS:
        return {
            "available": False,
            "reason": "DVF non disponible (Alsace-Moselle ou Mayotte)",
            "source": "etalab_geo_dvf",
            "dvf_app_url": DVF_APP_URL,
        }

    tlocal = type_local if filter_type else None
    candidates = _dvf_commune_candidates(
        code_commune,
        postcode=postcode,
        arrondissement_code=arrondissement_code,
    )

    def _merge_csv_sales(raw: str, cc: str) -> tuple[list[dict], list[int]]:
        return _sales_from_csv_rows(raw, code_commune=cc, type_local=tlocal)

    if sales is None:
        for cc in candidates:
            for year in ("2025", "2024", "2023"):
                raw = _fetch_commune_csv(cc, year)
                if not raw:
                    continue
                rows_sales, rows_years = _merge_csv_sales(raw, cc)
                if len(rows_sales) >= DVF_MIN_COMPARABLES:
                    sales = rows_sales
                    sale_years = rows_years
                    stats_code = cc
                    source_detail = f"commune_{year}" if cc == code_commune else f"arrondissement_{cc}_{year}"
                    break
            if sales and len(sales) >= DVF_MIN_COMPARABLES:
                break

        if (not sales or len(sales) < 15) and code_commune in MERGED_COMMUNE_ARRONDISSEMENTS:
            merged_sales: list[dict] = []
            merged_years: set[int] = set()
            for cc, raw in _iter_commune_csv_parallel(
                list(MERGED_COMMUNE_ARRONDISSEMENTS[code_commune]), ("2025", "2024")
            ):
                chunk, chunk_years = _merge_csv_sales(raw, cc)
                merged_sales.extend(chunk)
                merged_years.update(chunk_years)
                if len(merged_sales) >= 120:
                    break
            if len(merged_sales) >= 20:
                sales = merged_sales
                sale_years = sorted(merged_years)
                stats_code = code_commune
                source_detail = f"ville_{code_commune}"

        if not sales or len(sales) < 15:
            dept_sales: list[dict] = []
            dept_years: set[int] = set()
            skip = set(candidates)
            dept_codes = [cc for cc in _fetch_dept_commune_codes(dept, limit=80) if cc not in skip]
            for cc, raw in _iter_commune_csv_parallel(dept_codes, ("2025", "2024")):
                chunk, chunk_years = _merge_csv_sales(raw, cc)
                dept_sales.extend(chunk)
                dept_years.update(chunk_years)
                if len(dept_sales) >= 120:
                    break
            if len(dept_sales) >= 20:
                sales = dept_sales
                sale_years = sorted(dept_years)
                stats_code = code_commune
                source_detail = f"departement_{dept}"

    if not sales or len(sales) < DVF_MIN_COMPARABLES:
        return {
            "available": False,
            "reason": "Pas assez de ventes DVF récentes sur la zone",
            "code_commune": code_commune,
            "source": "etalab_geo_dvf",
            "dvf_app_url": DVF_APP_URL,
        }

    pool, geo_level, filter_detail = _pick_comparable_pool(
        sales,
        subject_surface=subject_surface,
        subject_lat=subject_lat,
        subject_lon=subject_lon,
        postcode=norm_postcode or postcode,
    )
    if len(pool) < DVF_MIN_COMPARABLES:
        pool = _iqr_filter_sales(sales)
        geo_level = "commune"
        filter_detail = "commune (repli — peu de ventes filtrées)"

    result = _stats_from_sales(
        pool,
        geo_level=geo_level,
        filter_detail=filter_detail,
        source_detail=source_detail,
        stats_code=stats_code,
        type_local=type_local,
        norm_postcode=norm_postcode,
        sale_years=sale_years,
        subject_surface=subject_surface,
        subject_lat=subject_lat,
        subject_lon=subject_lon,
    )
    result["raw_sales"] = _compact_sales_for_cache(sales)
    result["sale_years"] = sale_years
    _save_cached_stats(cache_key, type_local, result)
    # Ne pas renvoyer toute la base brute au client
    payload = {k: v for k, v in result.items() if k != "raw_sales"}
    return payload


def compare_listing_to_dvf(
    price: int | float | None,
    surface: float | None,
    address: str = "",
    city: str = "",
    *,
    sector: str | None = None,
    postcode: str | None = None,
    published_at: str | None = None,
    transaction_type: str = "vente",
    type_local: str | None = None,
) -> dict:
    """
    Compare le prix affiché d'une annonce au marché DVF local.
    Retourne verdict, écart %, lien DVF.
    """
    if transaction_type == "location":
        return {
            "available": False,
            "reason": "Comparatif DVF réservé aux ventes",
            "dvf_app_url": DVF_APP_URL,
        }

    if not price or not surface or surface <= 0:
        return {
            "available": False,
            "reason": "Prix ou surface manquant pour le comparatif",
            "dvf_app_url": DVF_APP_URL,
        }

    loc = parse_location_hint(address, city)
    city = loc["city"] or city or None
    postcode = postcode or loc["postcode"]
    arrondissement = loc["arrondissement_code"]
    sector = (sector or "").strip() or None
    if not sector and address:
        sector = extract_listing_location(address, None, city).get("sector")

    geo = geocode_address(address, city or "", prefer_street=bool(address and address.strip() not in ("—", "-")))
    code_commune = geo.get("citycode") if geo else arrondissement
    commune_name = geo.get("city") if geo else city
    if geo and geo.get("postcode"):
        postcode = geo["postcode"]
    if geo and geo.get("citycode"):
        arrondissement = geo["citycode"] if geo["citycode"] in (
            MERGED_COMMUNE_ARRONDISSEMENTS.get("75056", [])
            + MERGED_COMMUNE_ARRONDISSEMENTS.get("69123", [])
            + MERGED_COMMUNE_ARRONDISSEMENTS.get("13055", [])
        ) else arrondissement

    if not code_commune and city:
        resolved = resolve_commune_code(city, postcode)
        if resolved:
            code_commune = resolved.get("code")
            commune_name = resolved.get("nom") or city

    if not code_commune:
        return {
            "available": False,
            "reason": "Commune non identifiée — précisez l'adresse",
            "dvf_app_url": DVF_APP_URL,
        }

    tlocal = type_local or _guess_type_local(surface, address)
    subj_lat = geo.get("lat") if geo else None
    subj_lon = geo.get("lon") if geo else None
    stats = compute_price_stats(
        code_commune,
        tlocal,
        postcode=postcode,
        arrondissement_code=arrondissement,
        subject_surface=float(surface) if surface else None,
        subject_lat=float(subj_lat) if subj_lat is not None else None,
        subject_lon=float(subj_lon) if subj_lon is not None else None,
    )
    if not stats or not stats.get("available"):
        return {**(stats or {}), "listing_m2": round(price / surface, 0)}

    listing_m2 = price / surface
    median = stats["median_m2"]
    if not median:
        return {**stats, "available": False, "listing_m2": round(listing_m2, 0)}
    delta_pct = round((listing_m2 - median) / median * 100, 1)

    if delta_pct <= -12:
        verdict = "sous_marche"
        label = "Sous le marché DVF"
        opportunity = "high"
    elif delta_pct <= -5:
        verdict = "leger_sous_marche"
        label = "Légèrement sous le marché"
        opportunity = "medium"
    elif delta_pct >= 15:
        verdict = "sur_marche"
        label = "Au-dessus du marché DVF"
        opportunity = "low"
    else:
        verdict = "marche"
        label = "Aligné sur le marché DVF"
        opportunity = "neutral"

    return {
        "available": True,
        "code_commune": code_commune,
        "commune": commune_name,
        "sector": sector or commune_name,
        "postcode": postcode,
        "listing_city": city,
        "listing_sector": sector,
        "listing_postcode": postcode,
        "listing_published_at": (published_at or "")[:10] or None,
        "type_local": tlocal,
        "listing_price": int(price),
        "listing_m2": round(listing_m2, 0),
        "dvf_median_m2": median,
        "dvf_mean_m2": stats.get("mean_m2"),
        "dvf_sample_count": stats.get("sample_count"),
        "dvf_geo_level": stats.get("geo_level") or "commune",
        "dvf_filter_detail": stats.get("filter_detail"),
        "dvf_surface_band": stats.get("surface_band"),
        "dvf_comparables": stats.get("comparables") or [],
        "dvf_comparables_total": stats.get("comparables_total") or stats.get("sample_count"),
        "dvf_reference_period": stats.get("reference_period"),
        "dvf_reference_months": stats.get("reference_months"),
        "delta_pct": delta_pct,
        "verdict": verdict,
        "verdict_label": label,
        "opportunity": opportunity,
        "source": stats.get("source_detail"),
        "dvf_app_url": DVF_APP_URL,
        "compared_at": _now(),
    }


def dvf_summary_for_display(comp: dict) -> str:
    if not comp.get("available"):
        return comp.get("reason") or "DVF indisponible"
    sign = "+" if comp["delta_pct"] > 0 else ""
    return (
        f"{comp['verdict_label']} — {sign}{comp['delta_pct']}% vs médiane DVF "
        f"({comp['dvf_median_m2']:,.0f} €/m², {comp['dvf_sample_count']} ventes)".replace(",", " ")
    )
