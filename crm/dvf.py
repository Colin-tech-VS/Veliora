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
import re
import statistics
from datetime import date, datetime, timedelta, timezone
from typing import Any
import requests

logger = logging.getLogger(__name__)

GEO_COMMUNES = "https://geo.api.gouv.fr/communes"
GEO_SEARCH = "https://api-adresse.data.gouv.fr/search"
DVF_CSV_BASE = "https://files.data.gouv.fr/geo-dvf/latest/csv"
DVF_APP_URL = "https://app.dvf.etalab.gouv.fr/"

# Ventes DVF prises en compte (récentes = plus représentatives du marché actuel)
DVF_MAX_AGE_MONTHS = 24

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

    m = re.search(r"F-\d{5},\s*([^(]+)", addr, re.I)
    if m:
        sector = m.group(1).strip()
        if not loc.get("city"):
            loc["city"] = _normalize_city_name(sector)

    arr = _ARRONDISSEMENT_CITY_RE.search(text)
    if arr:
        sector = arr.group(0).strip()
        if not loc.get("city"):
            loc["city"] = arr.group(1).title()

    city = loc.get("city")
    if city and sector:
        city_norm = _normalize_city_name(city)
        sector_norm = sector.strip()
        if sector_norm.lower() == city_norm.lower():
            sector = city_norm
    elif city and not sector:
        sector = city
    elif sector and not city:
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


def _session() -> requests.Session:
    s = requests.Session()
    s.headers.update({"User-Agent": "Veliora/1.0 (pige immobiliere; contact@veliora.local)"})
    return s


def geocode_address(address: str, city: str = "") -> dict | None:
    """Géocode une adresse (API Adresse.data.gouv.fr)."""
    loc = parse_location_hint(address, city)
    if loc["postcode"] and loc["city"]:
        q = f"{loc['postcode']} {loc['city']}"
    elif loc["city"]:
        q = loc["city"]
    elif loc["postcode"]:
        q = loc["postcode"]
    else:
        q = ", ".join(p for p in (address, city) if p).strip()
    if not q or len(q) < 3:
        if loc["arrondissement_code"]:
            return {
                "label": q,
                "citycode": loc["arrondissement_code"],
                "postcode": loc["postcode"],
                "city": loc["city"],
                "lon": None,
                "lat": None,
            }
        return None
    try:
        r = _session().get(
            GEO_SEARCH,
            params={"q": q, "limit": 1},
            timeout=12,
        )
        r.raise_for_status()
        feats = r.json().get("features") or []
        if not feats:
            if loc["arrondissement_code"]:
                return {
                    "label": q,
                    "citycode": loc["arrondissement_code"],
                    "postcode": loc["postcode"],
                    "city": loc["city"],
                    "lon": None,
                    "lat": None,
                }
            return None
        props = feats[0].get("properties") or {}
        citycode = props.get("citycode") or loc["arrondissement_code"]
        return {
            "label": props.get("label"),
            "citycode": citycode,
            "postcode": props.get("postcode") or loc["postcode"],
            "city": props.get("city") or loc["city"],
            "lon": (feats[0].get("geometry") or {}).get("coordinates", [None, None])[0],
            "lat": (feats[0].get("geometry") or {}).get("coordinates", [None, None])[1],
        }
    except requests.RequestException as exc:
        logger.warning("Geocoding failed: %s", exc)
        if loc["arrondissement_code"]:
            return {
                "label": q,
                "citycode": loc["arrondissement_code"],
                "postcode": loc["postcode"],
                "city": loc["city"],
                "lon": None,
                "lat": None,
            }
        return None


def resolve_commune_code(city: str, postcode: str | None = None) -> dict | None:
    """Résout le code INSEE via geo.api.gouv.fr."""
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


def _prices_from_csv_rows(
    raw: str,
    *,
    code_commune: str | None = None,
    type_local: str | None = None,
    max_age_months: int = DVF_MAX_AGE_MONTHS,
) -> tuple[list[float], list[int]]:
    prices: list[float] = []
    years: set[int] = set()
    cutoff = date.today() - timedelta(days=30 * max(1, max_age_months))
    reader = csv.DictReader(io.StringIO(raw))
    for row in reader:
        if row.get("nature_mutation") != "Vente":
            continue
        tlocal = row.get("type_local") or ""
        if tlocal not in ("Appartement", "Maison"):
            continue
        if type_local and tlocal != type_local:
            continue
        if code_commune and row.get("code_commune") != code_commune:
            continue
        dm = (row.get("date_mutation") or "").strip()
        if dm:
            try:
                d = date.fromisoformat(dm[:10])
                if d < cutoff:
                    continue
                years.add(d.year)
            except ValueError:
                pass
        try:
            vf = float(row.get("valeur_fonciere") or 0)
            surf = float(row.get("surface_reelle_bati") or 0)
        except (TypeError, ValueError):
            continue
        if vf < 10_000 or surf < 9:
            continue
        pm2 = vf / surf
        if 500 <= pm2 <= 25_000:
            prices.append(pm2)
    return prices, sorted(years)


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
) -> dict | None:
    """
    Médiane et échantillon DVF pour une commune.
    Repli : arrondissements (Paris/Lyon/Marseille) puis médiane départementale.
    """
    if not code_commune:
        return None

    cache_key = arrondissement_code or _postcode_to_arrondissement(postcode) or code_commune
    cached = _load_cached_stats(cache_key, type_local)
    if cached:
        return cached
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
    prices: list[float] = []
    sale_years: list[int] = []
    source_detail = "commune"
    stats_code = cache_key

    def _merge_csv_prices(raw: str, cc: str) -> tuple[list[float], list[int]]:
        p, y = _prices_from_csv_rows(raw, code_commune=cc, type_local=tlocal)
        return p, y

    for cc in candidates:
        for year in ("2025", "2024", "2023"):
            raw = _fetch_commune_csv(cc, year)
            if not raw:
                continue
            rows_prices, rows_years = _merge_csv_prices(raw, cc)
            if len(rows_prices) >= 8:
                prices = rows_prices
                sale_years = rows_years
                stats_code = cc
                source_detail = f"commune_{year}" if cc == code_commune else f"arrondissement_{cc}_{year}"
                break
        if len(prices) >= 8:
            break

    if len(prices) < 15 and code_commune in MERGED_COMMUNE_ARRONDISSEMENTS:
        merged_prices: list[float] = []
        merged_years: set[int] = set()
        for cc in MERGED_COMMUNE_ARRONDISSEMENTS[code_commune]:
            raw = _fetch_commune_csv(cc, "2025") or _fetch_commune_csv(cc, "2024")
            if not raw:
                continue
            chunk, chunk_years = _merge_csv_prices(raw, cc)
            merged_prices.extend(chunk)
            merged_years.update(chunk_years)
            if len(merged_prices) >= 120:
                break
        if len(merged_prices) >= 20:
            prices = merged_prices
            sale_years = sorted(merged_years)
            stats_code = code_commune
            source_detail = f"ville_{code_commune}"

    if len(prices) < 15:
        dept_prices: list[float] = []
        dept_years: set[int] = set()
        skip = set(candidates)
        for cc in _fetch_dept_commune_codes(dept, limit=80):
            if cc in skip:
                continue
            raw = _fetch_commune_csv(cc, "2025") or _fetch_commune_csv(cc, "2024")
            if not raw:
                continue
            chunk, chunk_years = _merge_csv_prices(raw, cc)
            dept_prices.extend(chunk)
            dept_years.update(chunk_years)
            if len(dept_prices) >= 120:
                break
        if len(dept_prices) >= 20:
            prices = dept_prices
            sale_years = sorted(dept_years)
            stats_code = code_commune
            source_detail = f"departement_{dept}"

    if len(prices) < 8:
        result = {
            "available": False,
            "reason": "Pas assez de ventes DVF récentes sur la zone",
            "code_commune": code_commune,
            "source": "etalab_geo_dvf",
            "dvf_app_url": DVF_APP_URL,
        }
        return result

    median = statistics.median(prices)
    reference_period = _format_dvf_period(sale_years)
    result = {
        "available": True,
        "code_commune": stats_code,
        "type_local": type_local,
        "median_m2": round(median, 0),
        "mean_m2": round(statistics.mean(prices), 0),
        "sample_count": len(prices),
        "reference_period": reference_period,
        "reference_months": DVF_MAX_AGE_MONTHS,
        "source": "etalab_geo_dvf",
        "source_detail": source_detail,
        "dvf_app_url": DVF_APP_URL,
        "updated_at": _now(),
    }
    _save_cached_stats(cache_key, type_local, result)
    return result


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

    geo = geocode_address(address, city or "")
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
    stats = compute_price_stats(
        code_commune,
        tlocal,
        postcode=postcode,
        arrondissement_code=arrondissement,
    )
    if not stats or not stats.get("available"):
        return {**(stats or {}), "listing_m2": round(price / surface, 0)}

    listing_m2 = price / surface
    median = stats["median_m2"]
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
