"""Données carte : agence + prospects géocodés (cache + Google Geocoding API)."""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import time
import threading
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone

from crawler.storage import get_connection
from velora_db.config import is_postgres

logger = logging.getLogger(__name__)

_GEOCODE_MAX_PER_REQUEST = 30
_GEOCODE_TIME_BUDGET_SEC = 18.0
_ADDRESS_BAD = frozenset({"", "—", "-", "n/a", "non renseigné"})


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _norm_key(address: str) -> str:
    s = re.sub(r"\s+", " ", (address or "").strip().lower())
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


def google_maps_api_key() -> str:
    """Clé navigateur (Maps JavaScript) — restrictions référents HTTP."""
    return (os.getenv("GOOGLE_MAPS_API_KEY") or os.getenv("GOOGLE_MAPS_KEY") or "").strip()


def google_geocoding_api_key() -> str:
    """Clé serveur (Geocoding API) — restrictions IP Scalingo recommandées."""
    return (
        os.getenv("GOOGLE_GEOCODING_API_KEY")
        or os.getenv("GOOGLE_MAPS_SERVER_KEY")
        or google_maps_api_key()
    ).strip()


def maps_use_google_javascript() -> bool:
    """
    Carte tuiles Google dans le navigateur — nécessite facturation GCP active.
    Sans GOOGLE_MAPS_JS=true, Veliora affiche OpenStreetMap (gratuit, sans facturation).
  La clé sert quand même au géocodage serveur si les APIs répondent.
    """
    if not google_maps_api_key():
        return False
    return os.getenv("GOOGLE_MAPS_JS", "").strip().lower() in ("1", "true", "yes", "on")


def ensure_map_schema() -> None:
    with get_connection() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS geocode_cache (
                cache_key TEXT PRIMARY KEY,
                latitude REAL NOT NULL,
                longitude REAL NOT NULL,
                formatted_address TEXT,
                updated_at TEXT NOT NULL
            )
            """
        )
        if is_postgres():
            cur = conn.execute(
                """
                SELECT column_name FROM information_schema.columns
                WHERE table_name = 'leads' AND column_name IN ('latitude', 'longitude')
                """
            )
            cols = {r[0] if isinstance(r, (tuple, list)) else r["column_name"] for r in cur.fetchall()}
            if "latitude" not in cols:
                conn.execute("ALTER TABLE leads ADD COLUMN latitude DOUBLE PRECISION")
            if "longitude" not in cols:
                conn.execute("ALTER TABLE leads ADD COLUMN longitude DOUBLE PRECISION")
        else:
            lcols = {r[1] for r in conn.execute("PRAGMA table_info(leads)").fetchall()}
            if lcols:
                if "latitude" not in lcols:
                    conn.execute("ALTER TABLE leads ADD COLUMN latitude REAL")
                if "longitude" not in lcols:
                    conn.execute("ALTER TABLE leads ADD COLUMN longitude REAL")
        conn.commit()


def format_location_line(
    address: str | None,
    postcode: str | None = None,
    city: str | None = None,
) -> str | None:
    addr = (address or "").strip()
    if addr in _ADDRESS_BAD:
        addr = ""
    pc = (postcode or "").strip()
    ct = (city or "").strip()
    tail = " ".join(p for p in (pc, ct) if p)
    if addr and tail and tail.lower() not in addr.lower():
        return f"{addr}, {tail}, France"
    if addr:
        return f"{addr}, France" if "france" not in addr.lower() else addr
    if tail:
        return f"{tail}, France"
    return None


def _cache_get(key: str) -> tuple[float, float] | None:
    with get_connection() as conn:
        row = conn.execute(
            "SELECT latitude, longitude FROM geocode_cache WHERE cache_key = ?",
            (key,),
        ).fetchone()
    if not row:
        return None
    try:
        lat = row["latitude"]
        lng = row["longitude"]
    except (KeyError, TypeError, IndexError):
        lat, lng = row[0], row[1]
    if lat is None or lng is None:
        return None
    return float(lat), float(lng)


def _cache_set(key: str, lat: float, lng: float, formatted: str = "") -> None:
    with get_connection() as conn:
        conn.execute(
            """
            INSERT INTO geocode_cache (cache_key, latitude, longitude, formatted_address, updated_at)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(cache_key) DO UPDATE SET
                latitude = excluded.latitude,
                longitude = excluded.longitude,
                formatted_address = excluded.formatted_address,
                updated_at = excluded.updated_at
            """,
            (key, lat, lng, formatted, _now()),
        )
        conn.commit()


def _geocode_ban(query: str) -> tuple[float, float] | None:
    """Géocodage Base Adresse Nationale (api-adresse.data.gouv.fr).

    Officiel, gratuit, sans clé et pensé pour le volume — fiable depuis une IP
    datacenter (Scalingo), contrairement à Nominatim souvent bloqué/limité.
    Couvre adresses ET communes (fallback ville/CP).
    """
    q = (query or "").replace(", France", "").strip()
    if not q:
        return None
    params = urllib.parse.urlencode({"q": q, "limit": 1, "autocomplete": 0})
    url = f"https://api-adresse.data.gouv.fr/search/?{params}"
    req = urllib.request.Request(url, headers={"User-Agent": "Veliora-CRM/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
        logger.warning("BAN geocode failed for %s: %s", q[:80], exc)
        return None
    feats = payload.get("features") or []
    if not feats:
        return None
    coords = (feats[0].get("geometry") or {}).get("coordinates") or []
    if len(coords) != 2:
        return None
    # GeoJSON : [lon, lat]
    return float(coords[1]), float(coords[0])


def _geocode_nominatim(query: str) -> tuple[float, float] | None:
    """Géocodage OpenStreetMap (sans clé API)."""
    params = urllib.parse.urlencode(
        {
            "q": query,
            "format": "json",
            "limit": 1,
            "countrycodes": "fr",
        }
    )
    url = f"https://nominatim.openstreetmap.org/search?{params}"
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": "Veliora-CRM/1.0 (contact@veliora.fr)",
            "Accept-Language": "fr",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=14) as resp:
            rows = json.loads(resp.read().decode("utf-8"))
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
        logger.warning("nominatim geocode failed for %s: %s", query[:80], exc)
        return None
    if not rows:
        return None
    lat = float(rows[0]["lat"])
    lng = float(rows[0]["lon"])
    return lat, lng


def _geocode_google(query: str, api_key: str) -> tuple[float, float] | None:
    params = urllib.parse.urlencode(
        {"address": query, "key": api_key, "region": "fr", "language": "fr"}
    )
    url = f"https://maps.googleapis.com/maps/api/geocode/json?{params}"
    try:
        with urllib.request.urlopen(url, timeout=12) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
        logger.warning("google geocode failed for %s: %s", query[:80], exc)
        return None

    status = payload.get("status")
    if status != "OK" or not payload.get("results"):
        if status not in ("ZERO_RESULTS",):
            logger.info("geocode status %s for %s", status, query[:80])
        return None

    loc = payload["results"][0]["geometry"]["location"]
    return float(loc["lat"]), float(loc["lng"])


def geocode_query(query: str) -> tuple[float, float] | None:
    q = (query or "").strip()
    if not q or q.lower() in _ADDRESS_BAD:
        return None
    key = _norm_key(q)
    cached = _cache_get(key)
    if cached:
        return cached

    # 1) BAN (officiel FR, gratuit, fiable depuis datacenter) en premier.
    coords = _geocode_ban(q)
    # 2) Google Geocoding si clé serveur configurée.
    if not coords:
        geo_key = google_geocoding_api_key()
        if geo_key:
            coords = _geocode_google(q, geo_key)
    # 3) Nominatim en dernier repli.
    if not coords:
        coords = _geocode_nominatim(q)
    if not coords:
        return None

    _cache_set(key, coords[0], coords[1], q)
    return coords


def _lead_coords_from_row(row) -> tuple[float, float] | None:
    keys = row.keys()
    if "latitude" in keys and "longitude" in keys:
        lat, lng = row["latitude"], row["longitude"]
        if lat is not None and lng is not None:
            try:
                return float(lat), float(lng)
            except (TypeError, ValueError):
                pass
    return None


def _save_lead_coords(lead_id: int, agency_id: str, lat: float, lng: float) -> None:
    with get_connection() as conn:
        conn.execute(
            """
            UPDATE leads SET latitude = ?, longitude = ?, updated_at = ?
            WHERE id = ? AND agency_id = ?
            """,
            (lat, lng, _now(), lead_id, agency_id),
        )
        conn.commit()


def build_agency_map_point(agency_id: str) -> dict | None:
    from crm.mandates.storage import get_agency_legal_profile
    from crawler.storage import get_agency_name

    profile = get_agency_legal_profile(agency_id)
    name = (profile.get("brand_name") or profile.get("legal_name") or get_agency_name(agency_id) or "Votre agence").strip()
    line = format_location_line(
        profile.get("address"),
        profile.get("postal_code"),
        profile.get("city"),
    )
    if not line:
        return {
            "name": name,
            "address_line": "",
            "lat": None,
            "lng": None,
            "configured": False,
        }
    key = _norm_key(line)
    coords = _cache_get(key)
    if not coords:
        # Ne pas géocoder en synchrone (bloque GET /api/map → timeout Scalingo).
        # On programme un géocodage en arrière-plan ; la carte affiche l'agence au prochain refresh.
        schedule_agency_geocode(line)
    return {
        "name": name,
        "address_line": line.replace(", France", ""),
        "lat": coords[0] if coords else None,
        "lng": coords[1] if coords else None,
        "configured": bool(coords),
    }


def schedule_agency_geocode(line: str) -> None:
    """Géocode l'adresse agence en arrière-plan (alimente geocode_cache)."""
    line = (line or "").strip()
    if not line:
        return

    def _run() -> None:
        try:
            ensure_map_schema()
            geocode_query(line)
        except Exception:
            logger.exception("schedule_agency_geocode")

    threading.Thread(target=_run, daemon=True, name="agency-geo").start()


def build_map_payload(agency_id: str) -> dict:
    ensure_map_schema()
    api_key = google_maps_api_key()
    use_google_js = maps_use_google_javascript()

    from crawler.storage import get_agency_name

    agency = build_agency_map_point(agency_id)
    agency_name = get_agency_name(agency_id)

    with get_connection() as conn:
        rows = conn.execute(
            """
            SELECT id, address, city, postcode, latitude, longitude,
                   listing_title, price, mandate_score, score,
                   pipeline, transaction_type, surface
            FROM leads
            WHERE agency_id = ?
            ORDER BY mandate_score DESC, score DESC
            """,
            (agency_id,),
        ).fetchall()

    markers: list[dict] = []
    pending = 0
    no_location = 0
    geocoded_this_run = 0
    needs_background_geocode: list[tuple[int, str]] = []

    for row in rows:
        keys = row.keys()
        address = row["address"] if "address" in keys else None
        city = row["city"] if "city" in keys else None
        postcode = row["postcode"] if "postcode" in keys else None
        line = format_location_line(address, postcode, city)
        if not line:
            # Ni adresse, ni ville, ni CP exploitables → impossible à placer.
            no_location += 1
            continue

        coords = _lead_coords_from_row(row)
        if not coords:
            needs_background_geocode.append((int(row["id"]), line))
            pending += 1
            continue

        title = row["listing_title"] if "listing_title" in keys else None
        if not title or str(title).strip() in _ADDRESS_BAD:
            title = (address or "Annonce")[:120]

        markers.append(
            {
                "id": int(row["id"]),
                "lat": coords[0],
                "lng": coords[1],
                "title": str(title)[:120],
                "address": (address or "").strip() if address not in _ADDRESS_BAD else line,
                "price": int(row["price"] or 0),
                "mandate_score": int(row["mandate_score"] or 0) if "mandate_score" in keys else 0,
                "score": int(row["score"] or 0),
                "pipeline": row["pipeline"] if "pipeline" in keys else "nouveau",
                "transaction_type": row["transaction_type"] if "transaction_type" in keys else "vente",
                "surface": row["surface"],
            }
        )

    if needs_background_geocode:
        schedule_map_geocode_batch(agency_id, needs_background_geocode[:120])

    no_location_hint = None
    if no_location:
        no_location_hint = (
            f"{no_location} annonce(s) sans adresse exploitable (ni rue, ni ville, ni CP) "
            "— elles ne peuvent pas être placées. Recrawlez-les ou complétez la ville dans la fiche."
        )

    return {
        "ok": True,
        "maps_provider": "google" if use_google_js else "osm",
        "maps_api_key": api_key if use_google_js else "",
        "agency_name": agency_name,
        "agency": agency,
        "markers": markers,
        "stats": {
            "total_leads": len(rows),
            "on_map": len(markers),
            "pending_geocode": pending,
            "no_location": no_location,
            "geocoded_now": geocoded_this_run,
        },
        "hints": {
            "no_api_key": not bool(api_key),
            "using_osm": not use_google_js,
            "google_key_set_osm_mode": bool(api_key) and not use_google_js,
            "no_agency_address": not (agency or {}).get("address_line"),
            "refresh_hint": pending > 0,
            "no_location_hint": no_location_hint,
            "billing_hint": (
                "Clé Google détectée : carte en OpenStreetMap (gratuit). "
                "Pour Google Maps : activez la facturation GCP puis "
                "GOOGLE_MAPS_JS=true sur Scalingo."
                if api_key and not use_google_js
                else None
            ),
        },
    }


def schedule_map_geocode_batch(
    agency_id: str,
    items: list[tuple[int, str]],
) -> None:
    """Géocode les prospects sans coordonnées en arrière-plan (ne bloque pas GET /api/map)."""
    if not items:
        return

    def _run() -> None:
        try:
            ensure_map_schema()
            for lead_id, line in items:
                try:
                    with get_connection() as conn:
                        row = conn.execute(
                            "SELECT latitude, longitude FROM leads WHERE id = ? AND agency_id = ?",
                            (lead_id, agency_id),
                        ).fetchone()
                    if row and row["latitude"] is not None and row["longitude"] is not None:
                        continue
                    coords = geocode_query(line)
                    if coords:
                        _save_lead_coords(lead_id, agency_id, coords[0], coords[1])
                    # Throttle léger : BAN (FR) n'a pas la limite 1 req/s de Nominatim.
                    time.sleep(0.12)
                except Exception:
                    logger.exception("map geocode lead %s", lead_id)
        except Exception:
            logger.exception("schedule_map_geocode_batch")

    threading.Thread(
        target=_run, daemon=True, name=f"map-geo-{agency_id[:8]}"
    ).start()


def geocode_map_leads_sync(
    agency_id: str,
    max_items: int = _GEOCODE_MAX_PER_REQUEST,
) -> int:
    """Géocode jusqu'à N prospects (appel explicite Actualiser)."""
    ensure_map_schema()
    done = 0
    deadline = time.monotonic() + _GEOCODE_TIME_BUDGET_SEC
    with get_connection() as conn:
        rows = conn.execute(
            """
            SELECT id, address, city, postcode, latitude, longitude
            FROM leads
            WHERE agency_id = ?
              AND (latitude IS NULL OR longitude IS NULL)
            ORDER BY mandate_score DESC, score DESC
            LIMIT ?
            """,
            (agency_id, max(1, min(max_items, 60))),
        ).fetchall()
    for row in rows:
        if done >= max_items or time.monotonic() >= deadline:
            break
        line = format_location_line(row["address"], row["postcode"], row["city"])
        if not line:
            continue
        if _lead_coords_from_row(row):
            continue
        coords = geocode_query(line)
        if coords:
            _save_lead_coords(int(row["id"]), agency_id, coords[0], coords[1])
            done += 1
            time.sleep(0.12)
    return done


def schedule_lead_geocode(
    lead_id: int,
    agency_id: str,
    address: str | None,
    postcode: str | None = None,
    city: str | None = None,
) -> None:
    """Géocode en arrière-plan après crawl (pour la carte)."""
    line = format_location_line(address, postcode, city)
    if not line or not lead_id or not agency_id:
        return

    def _run() -> None:
        try:
            ensure_map_schema()
            with get_connection() as conn:
                row = conn.execute(
                    "SELECT latitude, longitude FROM leads WHERE id = ? AND agency_id = ?",
                    (lead_id, agency_id),
                ).fetchone()
            if row and row["latitude"] is not None and row["longitude"] is not None:
                return
            coords = geocode_query(line)
            if coords:
                _save_lead_coords(lead_id, agency_id, coords[0], coords[1])
        except Exception:
            logger.exception("schedule_lead_geocode %s", lead_id)

    threading.Thread(
        target=_run, daemon=True, name=f"lead-geo-{lead_id}"
    ).start()
