"""Données carte : agence + prospects géocodés (cache + Google Geocoding API)."""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone

from crawler.storage import get_connection
from velora_db.config import is_postgres

logger = logging.getLogger(__name__)

_GEOCODE_MAX_PER_REQUEST = 35
_ADDRESS_BAD = frozenset({"", "—", "-", "n/a", "non renseigné"})


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _norm_key(address: str) -> str:
    s = re.sub(r"\s+", " ", (address or "").strip().lower())
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


def google_maps_api_key() -> str:
    return (os.getenv("GOOGLE_MAPS_API_KEY") or os.getenv("GOOGLE_MAPS_KEY") or "").strip()


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
    return float(row[0]), float(row[1])


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


def geocode_query(query: str) -> tuple[float, float] | None:
    q = (query or "").strip()
    if not q or q.lower() in _ADDRESS_BAD:
        return None
    key = _norm_key(q)
    cached = _cache_get(key)
    if cached:
        return cached

    api_key = google_maps_api_key()
    if not api_key:
        return None

    params = urllib.parse.urlencode(
        {"address": q, "key": api_key, "region": "fr", "language": "fr"}
    )
    url = f"https://maps.googleapis.com/maps/api/geocode/json?{params}"
    try:
        with urllib.request.urlopen(url, timeout=12) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
        logger.warning("geocode failed for %s: %s", q[:80], exc)
        return None

    status = payload.get("status")
    if status != "OK" or not payload.get("results"):
        if status not in ("ZERO_RESULTS",):
            logger.info("geocode status %s for %s", status, q[:80])
        return None

    loc = payload["results"][0]["geometry"]["location"]
    lat, lng = float(loc["lat"]), float(loc["lng"])
    formatted = payload["results"][0].get("formatted_address") or q
    _cache_set(key, lat, lng, formatted)
    return lat, lng


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
    coords = geocode_query(line)
    return {
        "name": name,
        "address_line": line.replace(", France", ""),
        "lat": coords[0] if coords else None,
        "lng": coords[1] if coords else None,
        "configured": bool(coords),
    }


def build_map_payload(agency_id: str) -> dict:
    ensure_map_schema()
    api_key = google_maps_api_key()

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
    geocoded_this_run = 0

    for row in rows:
        keys = row.keys()
        address = row["address"] if "address" in keys else None
        city = row["city"] if "city" in keys else None
        postcode = row["postcode"] if "postcode" in keys else None
        line = format_location_line(address, postcode, city)
        if not line:
            pending += 1
            continue

        coords = _lead_coords_from_row(row)
        if not coords and geocoded_this_run < _GEOCODE_MAX_PER_REQUEST and api_key:
            coords = geocode_query(line)
            if coords:
                _save_lead_coords(int(row["id"]), agency_id, coords[0], coords[1])
                geocoded_this_run += 1

        if not coords:
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

    return {
        "ok": True,
        "maps_api_key": api_key,
        "agency_name": agency_name,
        "agency": agency,
        "markers": markers,
        "stats": {
            "total_leads": len(rows),
            "on_map": len(markers),
            "pending_geocode": pending,
            "geocoded_now": geocoded_this_run,
        },
        "hints": {
            "no_api_key": not bool(api_key),
            "no_agency_address": not (agency or {}).get("address_line"),
        },
    }
