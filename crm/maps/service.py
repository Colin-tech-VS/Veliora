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

_map_schema_ready = False
_map_schema_lock = threading.Lock()

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
    global _map_schema_ready
    if _map_schema_ready:
        return
    with _map_schema_lock:
        if _map_schema_ready:
            return
        _ensure_map_schema_once()
        _map_schema_ready = True


def _ensure_map_schema_once() -> None:
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


# Priorité de précision BAN : un numéro de rue est « exact », une commune ne
# l'est pas (centroïde). On préfère toujours le résultat le plus précis.
_BAN_TYPE_PRECISION = {
    "housenumber": 4,
    "street": 3,
    "locality": 2,
    "village": 2,
    "town": 2,
    "city": 2,
    "municipality": 1,
}


def _ban_request(params: dict) -> list[dict]:
    url = f"https://api-adresse.data.gouv.fr/search/?{urllib.parse.urlencode(params)}"
    req = urllib.request.Request(url, headers={"User-Agent": "Veliora-CRM/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
        logger.warning("BAN geocode failed for %s: %s", str(params.get("q"))[:80], exc)
        return []
    return payload.get("features") or []


def _geocode_ban(query: str) -> tuple[float, float] | None:
    """Géocodage Base Adresse Nationale (api-adresse.data.gouv.fr).

    Officiel, gratuit, sans clé et pensé pour le volume — fiable depuis une IP
    datacenter (Scalingo), contrairement à Nominatim souvent bloqué/limité.
    Couvre adresses ET communes (fallback ville/CP).

    Pour placer l'annonce à l'adresse EXACTE : on demande plusieurs candidats,
    on filtre par code postal quand il est connu, et on retient le résultat le
    plus précis (numéro de rue > rue > lieu-dit > commune). Sans cela, BAN
    pouvait renvoyer le centroïde de la commune même si la rue exacte existait.
    """
    q = (query or "").replace(", France", "").strip()
    if not q:
        return None

    pc_match = re.search(r"\b(\d{5})\b", q)
    postcode = pc_match.group(1) if pc_match else None

    params: dict = {"q": q, "limit": 6, "autocomplete": 0}
    if postcode:
        params["postcode"] = postcode
    feats = _ban_request(params)
    if not feats and postcode:
        # Le filtre code postal a tout exclu (CP/commune incohérents) → réessai libre.
        feats = _ban_request({"q": q, "limit": 6, "autocomplete": 0})
    if not feats:
        return None

    def _rank(feat: dict) -> tuple[int, float]:
        props = feat.get("properties") or {}
        precision = _BAN_TYPE_PRECISION.get(str(props.get("type") or "").lower(), 0)
        try:
            score = float(props.get("score") or 0.0)
        except (TypeError, ValueError):
            score = 0.0
        return (precision, score)

    best = max(feats, key=_rank)
    coords = (best.get("geometry") or {}).get("coordinates") or []
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


def _reverse_geocode_ban(lat: float, lng: float) -> str | None:
    params = urllib.parse.urlencode(
        {"lat": f"{lat:.6f}", "lon": f"{lng:.6f}", "limit": 1}
    )
    url = f"https://api-adresse.data.gouv.fr/reverse/?{params}"
    req = urllib.request.Request(url, headers={"User-Agent": "Veliora-CRM/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError):
        return None
    feats = payload.get("features") or []
    if not feats:
        return None
    props = feats[0].get("properties") or {}
    label = (props.get("label") or "").strip()
    return label or None


def reverse_geocode_city(lat: float, lng: float) -> dict[str, str | None]:
    params = urllib.parse.urlencode(
        {"lat": f"{lat:.6f}", "lon": f"{lng:.6f}", "limit": 1}
    )
    url = f"https://api-adresse.data.gouv.fr/reverse/?{params}"
    req = urllib.request.Request(url, headers={"User-Agent": "Veliora-CRM/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError):
        return {"city": None, "postcode": None, "label": None}
    feats = payload.get("features") or []
    if not feats:
        return {"city": None, "postcode": None, "label": None}
    props = feats[0].get("properties") or {}
    city = (props.get("city") or props.get("name") or "").strip() or None
    postcode = (props.get("postcode") or "").strip() or None
    label = (props.get("label") or "").strip() or None
    return {"city": city, "postcode": postcode, "label": label}


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


def _approx_address_label(postcode: str | None, city: str | None) -> str:
    from crawler.address_quality import format_approximate_address_label

    return format_approximate_address_label(city, postcode) or ""


def _save_lead_approx_address(
    lead_id: int,
    agency_id: str,
    approx_address: str,
) -> None:
    if not approx_address:
        return
    from crawler.address_quality import has_approximate_address_marker

    with get_connection() as conn:
        row = conn.execute(
            "SELECT address, city, postcode FROM leads WHERE id = ? AND agency_id = ?",
            (lead_id, agency_id),
        ).fetchone()
        if not row:
            return
        cur = (row["address"] or "").strip()
        if cur and has_approximate_address_marker(cur):
            return
        from crawler.address_quality import address_needs_approximate_fill

        if not address_needs_approximate_fill(
            cur,
            row["city"],
            row["postcode"],
        ):
            return
        conn.execute(
            """
            UPDATE leads
               SET address = ?, updated_at = ?
             WHERE id = ? AND agency_id = ?
            """,
            (approx_address, _now(), lead_id, agency_id),
        )
        conn.commit()


def _best_approx_address(
    lat: float,
    lng: float,
    postcode: str | None,
    city: str | None,
) -> str:
    reverse_label = _reverse_geocode_ban(lat, lng)
    if reverse_label:
        return f"{reverse_label} (approx.)"
    return _approx_address_label(postcode, city)


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
                   pipeline, transaction_type, surface, phone, email, type, status
            FROM leads
            WHERE agency_id = ?
              AND COALESCE(status, 'nouveau') != 'retire'
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
        approx_address = _best_approx_address(coords[0], coords[1], postcode, city)
        if (not address or str(address).strip() in _ADDRESS_BAD) and approx_address:
            _save_lead_approx_address(int(row["id"]), agency_id, approx_address)

        from crawler.hub_detection import parse_property_label

        raw_title = row["listing_title"] if "listing_title" in keys else None
        title = parse_property_label(
            raw_title,
            address if address not in _ADDRESS_BAD else None,
            surface=row["surface"] if "surface" in keys else None,
        )
        if not title or str(title).strip() in _ADDRESS_BAD:
            title = (raw_title or address or "Annonce")[:120]

        mscore = int(row["mandate_score"] or 0) if "mandate_score" in keys else 0
        from crm.scoring.probability import signature_probability

        sig = signature_probability(
            {
                "phone": row["phone"] if "phone" in keys else None,
                "email": row["email"] if "email" in keys else None,
            },
            mscore,
        )

        markers.append(
            {
                "id": int(row["id"]),
                "lat": coords[0],
                "lng": coords[1],
                "title": str(title)[:120],
                "address": (address or "").strip() if address not in _ADDRESS_BAD else approx_address or line,
                "location_precision": "precise" if address and address not in _ADDRESS_BAD else "approx",
                "price": int(row["price"] or 0),
                "mandate_score": mscore,
                "signature_probability": sig["probability"],
                "score": int(row["score"] or 0),
                "pipeline": row["pipeline"] if "pipeline" in keys else "nouveau",
                "transaction_type": row["transaction_type"] if "transaction_type" in keys else "vente",
                "surface": row["surface"],
                "type": (row["type"] if "type" in keys else "particulier") or "particulier",
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
                        approx = _best_approx_address(coords[0], coords[1], None, None)
                        _save_lead_approx_address(lead_id, agency_id, approx)
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
                approx = _best_approx_address(coords[0], coords[1], postcode, city)
                _save_lead_approx_address(lead_id, agency_id, approx)
        except Exception:
            logger.exception("schedule_lead_geocode %s", lead_id)

    threading.Thread(
        target=_run, daemon=True, name=f"lead-geo-{lead_id}"
    ).start()
