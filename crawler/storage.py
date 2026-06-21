"""Persistance Veliora — SQLite local ou Supabase PostgreSQL (DATABASE_URL)."""

from __future__ import annotations

import copy
import json
import logging
import re
import sqlite3
import threading
import time
import uuid
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import os

from velora_db import backup_database, checkpoint_database, db_status, get_connection, row_scalar
from velora_db.config import is_postgres, sqlite_path

from crawler.adapters import DEFAULT_SOURCES
from crawler.extractors import LeadData

DB_PATH = sqlite_path()

logger = logging.getLogger(__name__)

# Colonnes liste CRM (sans facts_audit — gros JSON inutile en tableau).
_LEADS_LIST_SQL = """
    id, first_name, last_name, phone, email, address, city, postcode, sector,
    surface, price, transaction_type, price_period, published_at, source, source_id,
    source_url, status, pipeline, listing_type, type, agency, agency_id, score,
    mandate_score, mandate_score_reason, previous_price, notes, next_follow_up,
    dvf_median_m2, dvf_delta_pct, dvf_verdict, dvf_verdict_label, dvf_commune,
    dvf_sample_count, dvf_compared_at, dvf_sector, dvf_reference_period,
    listing_title, price_change_count, last_price_change_at, priority_tier,
    score_explanation, scores_computed_at, latitude, longitude, listing_image_url,
    image_custom, image_updated_at, relisted_at, missing_fields, created_at, updated_at
""".strip()

# Colonnes minimales pour les compteurs dashboard (sans parsing lourd).
_STATS_LEADS_SQL = """
    agency_id, listing_type, type, status, city, postcode, sector, address
""".strip()

_SOURCES_CACHE_TTL_SEC = 50.0
_sources_cache: dict[str, tuple[float, list[dict]]] = {}

# Instantané court de la liste de leads. Au chargement du CRM, /api/bootstrap
# calcule get_leads(), puis /api/radar/summary le recalcule ~1 s plus tard pour
# le briefing (consultatif, jamais renvoyé comme liste à l'écran). On réutilise
# donc l'instantané frais au lieu de refaire toute la requête + l'enrichissement.
# Seul le radar lit cet instantané (opt-in) ; les endpoints de liste restent
# toujours frais. Invalidé à chaque mutation de lead.
_LEADS_SNAPSHOT_TTL_SEC = float(os.getenv("LEADS_SNAPSHOT_TTL", "30"))
_leads_snapshot: dict[str, tuple[float, list[dict]]] = {}


def invalidate_leads_snapshot(agency_id: str | None = None) -> None:
    if agency_id:
        _leads_snapshot.pop(agency_id, None)
    else:
        _leads_snapshot.clear()

_SETTINGS_CACHE_TTL_SEC = float(os.getenv("AGENCY_SETTINGS_CACHE_TTL", "90"))
_settings_cache: dict[str, tuple[float, dict]] = {}

_LISTING_TITLE_HINT_RE = re.compile(
    r"\b(acheter|achat|vente|vendre|location|louer|appartement|maison|terrain|immobilier|pi[eè]ce|m2|m²)\b",
    re.IGNORECASE,
)
_CITY_FROM_TITLE_RE = re.compile(
    r"\b(?:à|a)\s+([A-Za-zÀ-ÿ][A-Za-zÀ-ÿ' -]{1,50}?)\s*\((\d{5})\)",
    re.IGNORECASE,
)
_CITY_CP_PAREN_RE = re.compile(r"^\s*([A-Za-zÀ-ÿ][A-Za-zÀ-ÿ' -]{1,80})\s*\((\d{5})\)\s*$")
_CITY_CP_PREFIX_RE = re.compile(r"^\s*(\d{5})\s+([A-Za-zÀ-ÿ][A-Za-zÀ-ÿ' -]{1,80})\s*$")
_CITY_CP_SUFFIX_RE = re.compile(r"^\s*([A-Za-zÀ-ÿ][A-Za-zÀ-ÿ' -]{1,80})\s+(\d{5})\s*$")


def invalidate_agency_settings_cache(agency_id: str | None = None) -> None:
    if agency_id:
        _settings_cache.pop(agency_id, None)
    else:
        _settings_cache.clear()


def invalidate_sources_cache(agency_id: str | None = None) -> None:
    if agency_id:
        _sources_cache.pop(agency_id, None)
    else:
        _sources_cache.clear()


def get_db_path() -> Path:
    return DB_PATH


def _compute_property_fingerprint(
    postcode: str | None, surface: float | None, price: int | None
) -> str | None:
    """Empreinte cross-portail : CP + surface (±2.5 m²) + prix (±5 000 €)."""
    pc = (postcode or "").strip()
    if not pc or not surface or not price:
        return None
    try:
        surf_bucket = round(float(surface) / 5) * 5
        price_bucket = round(int(price) / 10000) * 10000
    except (TypeError, ValueError):
        return None
    if surf_bucket < 5 or price_bucket < 1000:
        return None
    return f"{pc}_{surf_bucket}_{price_bucket}"


_DEDUP_STREET_STOP = frozenset({
    "rue", "avenue", "boulevard", "impasse", "allee", "chemin", "place", "cours",
    "quai", "route", "residence", "appartement", "appart", "maison", "vente",
    "achat", "location", "voie", "passage", "square", "ville", "centre",
})


def _surface_bucket(surface: float | None) -> int | None:
    """Tranche surface (pas de 5 m²) pour le rapprochement, ou None si invalide."""
    try:
        b = round(float(surface) / 5) * 5
    except (TypeError, ValueError):
        return None
    return b if b >= 5 else None


def _address_signature(address: str | None) -> str | None:
    """Signature « numéro + rue » pour distinguer deux biens voisins d'un même CP.

    Renvoie None si l'adresse est absente/trop vague (ville seule) : dans ce cas le
    lead reste « compatible » avec n'importe quel autre (cross-portail où l'adresse
    exacte est masquée, ex. leboncoin).
    """
    import unicodedata

    a = (address or "").strip().lower()
    if not a or a == "—" or len(a) < 6:
        return None
    a = "".join(
        c for c in unicodedata.normalize("NFD", a) if unicodedata.category(c) != "Mn"
    )
    num_m = re.search(r"\b(\d{1,4})(?:\s*(?:bis|ter|quater))?\b", a)
    num = num_m.group(1) if num_m else ""
    # Sans numéro de voie, l'adresse (ville/rue seule) est trop vague pour distinguer
    # deux biens : on la traite comme « compatible » (None) plutôt que de scinder à tort.
    if not num:
        return None
    words = [w for w in re.findall(r"[a-z]{4,}", a) if w not in _DEDUP_STREET_STOP]
    return (f"{num}-" + "-".join(words[:2])).strip("-") or None


def _looks_like_listing_title(text: str | None) -> bool:
    t = (text or "").strip()
    if not t:
        return False
    if _LISTING_TITLE_HINT_RE.search(t):
        return True
    # Cas typique : "Acheter appartement à X (75000)" stocké en adresse/ville.
    if _CITY_FROM_TITLE_RE.search(t):
        return True
    return False


def _clean_lead_location_fields(lead: LeadData) -> None:
    """Évite que titre d'annonce/nav soit persisté en adresse/ville/CP."""
    from crawler.hub_detection import is_hub_listing_address

    raw_title = ((lead.raw_extras or {}).get("listing_title") or "").strip()
    addr = (lead.address or "").strip()
    city = (lead.city or "").strip()
    postcode = (lead.postcode or "").strip()

    if addr and (is_hub_listing_address(addr) or _looks_like_listing_title(addr)):
        addr = ""
    elif addr:
        from crawler.address_quality import is_city_only_address

        if is_city_only_address(addr, city, postcode):
            addr = ""
    if city and _looks_like_listing_title(city):
        city = ""
    if postcode and not re.fullmatch(r"\d{5}", postcode):
        m_pc = re.search(r"\b(\d{5})\b", postcode)
        postcode = m_pc.group(1) if m_pc else ""

    # Si ville/CP absents, tente extraction sûre depuis adresse ou titre.
    loc_text = addr or raw_title
    m = _CITY_FROM_TITLE_RE.search(loc_text or "")
    if m:
        if not city:
            city = m.group(1).strip()
        if not postcode:
            postcode = m.group(2)

    lead.address = addr or None
    lead.city = city or None
    lead.postcode = postcode or None
    from crawler.address_quality import sanitize_lead_commune_fields

    sanitize_lead_commune_fields(lead)


def _clean_location_values(
    address: str | None,
    city: str | None,
    postcode: str | None,
    listing_title: str | None = None,
) -> tuple[str | None, str | None, str | None]:
    addr = (address or "").strip()
    ct = (city or "").strip()
    pc = (postcode or "").strip()
    title = (listing_title or "").strip()

    if addr and _looks_like_listing_title(addr):
        addr = ""
    from crawler.address_quality import looks_like_street_in_commune_field

    if ct and (_looks_like_listing_title(ct) or looks_like_street_in_commune_field(ct)):
        ct = ""
    if pc and not re.fullmatch(r"\d{5}", pc):
        m_pc = re.search(r"\b(\d{5})\b", pc)
        pc = m_pc.group(1) if m_pc else ""

    m = _CITY_FROM_TITLE_RE.search(" ".join(p for p in (addr, title, ct) if p))
    if m:
        if not ct:
            ct = m.group(1).strip()
        if not pc:
            pc = m.group(2)

    from crawler.address_quality import sanitize_location_triplet

    return sanitize_location_triplet(addr or None, ct or None, pc or None)


def _persist_recrawl_repairs(lead: LeadData, existing_row: dict, agency_id: str) -> bool:
    """Recrawl : persiste les champs réparés même si la vérification stricte échoue."""
    lead_city = getattr(lead, "city", None)
    lead_postcode = getattr(lead, "postcode", None)
    lead_sector = getattr(lead, "sector", None)
    fields = {
        "first_name": lead.first_name,
        "last_name": lead.last_name,
        "phone": lead.phone,
        "email": lead.email,
        "address": lead.address,
        "city": lead_city,
        "postcode": lead_postcode,
        "sector": lead_sector,
        "surface": lead.surface,
        "price": lead.price,
        "transaction_type": lead.transaction_type,
        "price_period": lead.price_period,
        "published_at": lead.published_at,
        "agency": lead.agency,
        "listing_type": lead.type,
    }
    row_keys = {
        "first_name": existing_row.get("first_name"),
        "last_name": existing_row.get("last_name"),
        "phone": existing_row.get("phone") if existing_row.get("phone") != "—" else None,
        "email": existing_row.get("email") if existing_row.get("email") != "—" else None,
        "address": existing_row.get("address") if existing_row.get("address") != "—" else None,
        "city": existing_row.get("city"),
        "postcode": existing_row.get("postcode"),
        "sector": existing_row.get("sector"),
        "surface": existing_row.get("surface"),
        "price": existing_row.get("price") or None,
        "transaction_type": existing_row.get("transaction_type"),
        "price_period": existing_row.get("price_period"),
        "published_at": existing_row.get("published_at"),
        "agency": existing_row.get("agency"),
        "listing_type": existing_row.get("listing_type") or existing_row.get("type"),
    }
    if fields == row_keys:
        return False

    loc_changed = (
        (row_keys.get("address") or "").strip().lower() != (fields.get("address") or "").strip().lower()
        or row_keys.get("city") != fields.get("city")
        or row_keys.get("postcode") != fields.get("postcode")
        or row_keys.get("sector") != fields.get("sector")
    )
    geo_touch = ", latitude = NULL, longitude = NULL" if loc_changed else ""
    now = _now()
    with get_connection() as conn:
        conn.execute(
            f"""UPDATE leads SET
               first_name = ?, last_name = ?, phone = ?, email = ?,
               address = ?, city = ?, postcode = ?, sector = ?,
               surface = ?, price = ?, transaction_type = ?, price_period = ?,
               published_at = ?, agency = ?, listing_type = ?, updated_at = ?{geo_touch}
               WHERE id = ?""",
            (
                fields["first_name"],
                fields["last_name"],
                fields["phone"],
                fields["email"],
                fields["address"],
                fields["city"],
                fields["postcode"],
                fields["sector"],
                fields["surface"],
                fields["price"],
                fields["transaction_type"],
                fields["price_period"],
                fields["published_at"],
                fields["agency"],
                fields["listing_type"],
                now,
                existing_row["id"],
            ),
        )
        conn.commit()
    return True


def _canonicalize_city_postcode_values(
    city: str | None,
    postcode: str | None,
) -> tuple[str | None, str | None]:
    ct = (city or "").strip()
    pc = (postcode or "").strip()

    def _extract(text: str) -> tuple[str, str] | None:
        if not text:
            return None
        m = _CITY_CP_PAREN_RE.match(text)
        if m:
            return m.group(1).strip(), m.group(2)
        m = _CITY_CP_PREFIX_RE.match(text)
        if m:
            return m.group(2).strip(), m.group(1)
        m = _CITY_CP_SUFFIX_RE.match(text)
        if m:
            return m.group(1).strip(), m.group(2)
        return None

    parsed_city = _extract(ct)
    if parsed_city:
        ct, parsed_pc = parsed_city
        if not pc:
            pc = parsed_pc
    else:
        parsed_pc_field = _extract(pc)
        if parsed_pc_field:
            parsed_ct, parsed_pc = parsed_pc_field
            if not ct:
                ct = parsed_ct
            pc = parsed_pc

    if pc and not re.fullmatch(r"\d{5}", pc):
        m_pc = re.search(r"\b(\d{5})\b", pc)
        pc = m_pc.group(1) if m_pc else ""

    return (ct or None, pc or None)


def _attach_estimates(leads: list[dict], agency_id: str) -> None:
    """Rattache price_estimate / price_estimate_at depuis lead_estimates (1 requête)."""
    if not leads:
        return
    try:
        from crm.estimator.storage import get_estimates_for_lead_ids

        ids = [int(l["id"]) for l in leads if l.get("id") is not None]
        est = get_estimates_for_lead_ids(ids)
        if not est:
            return
        for lead in leads:
            lid = lead.get("id")
            if lid is not None and int(lid) in est:
                payload, at = est[int(lid)]
                lead["price_estimate"] = payload
                lead["price_estimate_at"] = at
    except Exception:
        logger.warning("attach_estimates ignoré", exc_info=False)


def _annotate_dedup(leads: list[dict]) -> list[dict]:
    """Annonce les doublons cross-portail SANS jamais masquer de lead.

    Important : aucun prospect ne doit disparaître de la liste (sauf suppression
    explicite par l'utilisateur). On regroupe les fiches d'un même bien grâce à
    plusieurs signaux, puis on annote (badge) la mieux scorée d'un groupe réellement
    multi-portails :

    - Empreinte « CP + surface + prix » (rapprochement cross-portail).
    - Garde adresse : dans un même bucket, deux adresses « numéro+rue » DIFFÉRENTES
      ne sont PAS fusionnées (évite de confondre deux appartements voisins). Une
      adresse absente reste compatible (cas leboncoin où l'adresse exacte est masquée).
    - Contact + surface : même téléphone/email ET même surface = même bien reposté
      (rattrape les cas où le prix a changé d'un portail à l'autre).
    """
    from collections import defaultdict

    n = len(leads)
    parent = list(range(n))

    def find(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    def union(i: int, j: int) -> None:
        ri, rj = find(i), find(j)
        if ri != rj:
            parent[max(ri, rj)] = min(ri, rj)

    # 1) Empreinte propriété, scindée par signature d'adresse (anti-faux-voisins).
    by_fp: dict[str, list[int]] = defaultdict(list)
    for idx, lead in enumerate(leads):
        fp = lead.get("property_fingerprint")
        if fp:
            by_fp[fp].append(idx)
    for idxs in by_fp.values():
        if len(idxs) < 2:
            continue
        sigs = {
            _address_signature(leads[i].get("address")) for i in idxs
        } - {None}
        # Deux adresses « numéro+rue » distinctes dans le même bucket → biens voisins
        # différents : on ne les rapproche pas. (0 ou 1 signature = compatible.)
        if len(sigs) > 1:
            continue
        first = idxs[0]
        for j in idxs[1:]:
            union(first, j)

    # 2) Contact + surface : même tél/email ET même surface = même bien.
    by_contact: dict[tuple, list[int]] = defaultdict(list)
    for idx, lead in enumerate(leads):
        surf = _surface_bucket(lead.get("surface"))
        if not surf:
            continue
        phone = re.sub(r"\D", "", str(lead.get("phone") or ""))
        email = str(lead.get("email") or "").strip().lower()
        if phone and len(phone) >= 9 and phone != "—":
            by_contact[("p", phone, surf)].append(idx)
        if email and "@" in email and email != "—":
            by_contact[("e", email, surf)].append(idx)
    for idxs in by_contact.values():
        first = idxs[0]
        for j in idxs[1:]:
            union(first, j)

    # 3) Annotation des groupes réellement multi-portails.
    groups: dict[int, list[dict]] = defaultdict(list)
    for idx, lead in enumerate(leads):
        groups[find(idx)].append(lead)
    for group in groups.values():
        sources = {(l.get("source") or "") for l in group}
        if len(group) > 1 and len([s for s in sources if s]) > 1:
            canonical = max(group, key=lambda l: l.get("mandate_score") or 0)
            canonical["_also_on"] = sorted(s for s in sources if s)
            canonical["_portal_count"] = len(group)

    return sorted(leads, key=lambda l: l.get("created_at") or "", reverse=True)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _coerce_timestamp(value: str | None) -> str:
    """Valeur sûre pour colonnes TIMESTAMPTZ (Postgres) ou TEXT (SQLite)."""
    if value is None:
        return _now()
    s = str(value).strip()
    return s if s else _now()


def _iso_date_prefix(value) -> str:
    """Extrait YYYY-MM-DD (Postgres datetime/date ou TEXT SQLite)."""
    if value is None:
        return ""
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    s = str(value).strip()
    if not s:
        return ""
    if "T" in s:
        s = s.split("T", 1)[0]
    return s[:10] if len(s) >= 10 else s


def _iso_datetime_str(value) -> str | None:
    """Normalise en chaîne ISO pour JSON / comparaisons."""
    if value is None:
        return None
    if isinstance(value, datetime):
        dt = value.astimezone(timezone.utc) if value.tzinfo else value.replace(tzinfo=timezone.utc)
        return dt.isoformat().replace("+00:00", "Z")
    if isinstance(value, date):
        return value.isoformat()
    s = str(value).strip()
    return s or None


def _sql_dvf_not_compared_clause() -> str:
    """Filtre « jamais comparé DVF » — Postgres n'accepte pas dvf_compared_at = ''."""
    if is_postgres():
        return "dvf_compared_at IS NULL"
    return "(dvf_compared_at IS NULL OR dvf_compared_at = '')"


def _migrate(conn: sqlite3.Connection) -> None:
    tables = {r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
    ).fetchall()}

    if "crawl_lead_changes" not in tables:
        _ensure_crawl_lead_changes_table(conn)

    if "crawl_jobs" not in tables:
        conn.execute("""
            CREATE TABLE crawl_jobs (
                id TEXT PRIMARY KEY,
                source_id TEXT,
                target_url TEXT NOT NULL,
                job_type TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending',
                progress INTEGER NOT NULL DEFAULT 0,
                leads_found INTEGER NOT NULL DEFAULT 0,
                leads_saved INTEGER NOT NULL DEFAULT 0,
                errors TEXT NOT NULL DEFAULT '[]',
                warnings TEXT NOT NULL DEFAULT '[]',
                message TEXT,
                started_at TEXT,
                finished_at TEXT,
                created_at TEXT NOT NULL
            )
        """)

    cols = {r[1] for r in conn.execute("PRAGMA table_info(leads)").fetchall()}
    if cols:
        if "updated_at" not in cols:
            conn.execute("ALTER TABLE leads ADD COLUMN updated_at TEXT")
            conn.execute("UPDATE leads SET updated_at = created_at WHERE updated_at IS NULL")
        if "source_id" not in cols:
            conn.execute("ALTER TABLE leads ADD COLUMN source_id TEXT")
        if "listing_type" not in cols:
            conn.execute("ALTER TABLE leads ADD COLUMN listing_type TEXT DEFAULT 'particulier'")
            if "type" in cols:
                conn.execute("UPDATE leads SET listing_type = type WHERE listing_type IS NULL")
        if "transaction_type" not in cols:
            conn.execute("ALTER TABLE leads ADD COLUMN transaction_type TEXT DEFAULT 'vente'")
        if "price_period" not in cols:
            conn.execute("ALTER TABLE leads ADD COLUMN price_period TEXT")
        if "published_at" not in cols:
            conn.execute("ALTER TABLE leads ADD COLUMN published_at TEXT")

    scols = {r[1] for r in conn.execute("PRAGMA table_info(sources)").fetchall()}
    if scols:
        if "is_custom" not in scols:
            conn.execute("ALTER TABLE sources ADD COLUMN is_custom INTEGER DEFAULT 0")
        if "created_at" not in scols:
            conn.execute("ALTER TABLE sources ADD COLUMN created_at TEXT")
        if "updated_at" not in scols:
            conn.execute("ALTER TABLE sources ADD COLUMN updated_at TEXT")
        if "logo_url" not in scols:
            conn.execute("ALTER TABLE sources ADD COLUMN logo_url TEXT")
        if "logo_fallback" not in scols:
            conn.execute("ALTER TABLE sources ADD COLUMN logo_fallback TEXT")
        if "domain" not in scols:
            conn.execute("ALTER TABLE sources ADD COLUMN domain TEXT")
        if "agency_id" not in scols:
            conn.execute("ALTER TABLE sources ADD COLUMN agency_id TEXT")

    logcols = {r[1] for r in conn.execute("PRAGMA table_info(crawl_logs)").fetchall()}
    if logcols and "job_id" not in logcols:
        conn.execute("ALTER TABLE crawl_logs ADD COLUMN job_id TEXT")

    jcols = {r[1] for r in conn.execute("PRAGMA table_info(crawl_jobs)").fetchall()}
    if jcols:
        for col, typedef in (
            ("city", "TEXT"),
            ("eta_seconds", "INTEGER"),
            ("listings_total", "INTEGER"),
            ("listings_done", "INTEGER"),
            ("leads_updated", "INTEGER DEFAULT 0"),
        ):
            if col not in jcols:
                conn.execute(f"ALTER TABLE crawl_jobs ADD COLUMN {col} {typedef}")
        if "agency_id" not in jcols:
            conn.execute("ALTER TABLE crawl_jobs ADD COLUMN agency_id TEXT")

    lcols = {r[1] for r in conn.execute("PRAGMA table_info(leads)").fetchall()}
    if lcols and "agency_id" not in lcols:
        conn.execute("ALTER TABLE leads ADD COLUMN agency_id TEXT")

    acols = {r[1] for r in conn.execute("PRAGMA table_info(activities)").fetchall()}
    if acols and "agency_id" not in acols:
        conn.execute("ALTER TABLE activities ADD COLUMN agency_id TEXT")

    _migrate_leads_drop_global_url_unique(conn)

    lcols = {r[1] for r in conn.execute("PRAGMA table_info(leads)").fetchall()}
    if lcols:
        for col, typedef in (
            ("mandate_score", "INTEGER DEFAULT 0"),
            ("mandate_score_reason", "TEXT"),
            ("previous_price", "INTEGER"),
            ("notes", "TEXT"),
            ("next_follow_up", "TEXT"),
            ("dvf_median_m2", "INTEGER"),
            ("dvf_delta_pct", "REAL"),
            ("dvf_verdict", "TEXT"),
            ("dvf_verdict_label", "TEXT"),
            ("dvf_commune", "TEXT"),
            ("dvf_sample_count", "INTEGER"),
            ("dvf_compared_at", "TEXT"),
            ("listing_title", "TEXT"),
            ("facts_audit", "TEXT"),
            ("city", "TEXT"),
            ("postcode", "TEXT"),
            ("sector", "TEXT"),
            ("dvf_sector", "TEXT"),
            ("dvf_reference_period", "TEXT"),
            ("price_change_count", "INTEGER DEFAULT 0"),
            ("last_price_change_at", "TEXT"),
            ("priority_tier", "TEXT"),
            ("score_explanation", "TEXT"),
            ("scores_computed_at", "TEXT"),
            ("latitude", "REAL"),
            ("longitude", "REAL"),
            ("listing_image_url", "TEXT"),
            ("image_custom", "INTEGER NOT NULL DEFAULT 0"),
            ("image_updated_at", "TEXT"),
            ("relisted_at", "TEXT"),
        ):
            if col not in lcols:
                conn.execute(f"ALTER TABLE leads ADD COLUMN {col} {typedef}")

    conn.execute("""
        CREATE TABLE IF NOT EXISTS dvf_commune_cache (
            cache_key TEXT PRIMARY KEY,
            payload TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS agency_settings (
            agency_id TEXT PRIMARY KEY,
            target_cities TEXT NOT NULL DEFAULT '[]',
            target_neighborhoods TEXT NOT NULL DEFAULT '[]',
            mandate_goal_month INTEGER NOT NULL DEFAULT 5,
            onboarding_step INTEGER NOT NULL DEFAULT 0,
            onboarding_completed INTEGER NOT NULL DEFAULT 0,
            updated_at TEXT
        )
    """)
    setcols = {r[1] for r in conn.execute("PRAGMA table_info(agency_settings)").fetchall()}
    if setcols:
        if "onboarding_step" not in setcols:
            conn.execute(
                "ALTER TABLE agency_settings ADD COLUMN onboarding_step INTEGER NOT NULL DEFAULT 0"
            )
        if "onboarding_completed" not in setcols:
            conn.execute(
                "ALTER TABLE agency_settings ADD COLUMN onboarding_completed INTEGER NOT NULL DEFAULT 0"
            )

    conn.execute("""
        CREATE TABLE IF NOT EXISTS password_reset_tokens (
            token TEXT PRIMARY KEY,
            user_id TEXT NOT NULL,
            email TEXT NOT NULL,
            expires_at TEXT NOT NULL,
            used INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS lead_price_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            lead_id INTEGER NOT NULL,
            agency_id TEXT NOT NULL,
            price INTEGER NOT NULL,
            recorded_at TEXT NOT NULL
        )
    """)
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_price_history_lead "
        "ON lead_price_history(lead_id, agency_id)"
    )

    conn.execute("""
        CREATE TABLE IF NOT EXISTS lead_outcomes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            lead_id INTEGER NOT NULL,
            agency_id TEXT NOT NULL,
            outcome_type TEXT NOT NULL,
            outcome_at TEXT NOT NULL,
            agent_id TEXT,
            notes TEXT,
            scores_snapshot TEXT
        )
    """)
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_lead_outcomes_lead "
        "ON lead_outcomes(lead_id, agency_id, outcome_at DESC)"
    )

    conn.execute("""
        CREATE TABLE IF NOT EXISTS agency_scoring_weights (
            agency_id TEXT PRIMARY KEY,
            weights_json TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
    """)

    conn.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_leads_agency_url "
        "ON leads(agency_id, source_url)"
    )

    conn.execute("""
        CREATE TABLE IF NOT EXISTS agencies (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            slug TEXT UNIQUE NOT NULL,
            email TEXT UNIQUE NOT NULL,
            created_at TEXT NOT NULL
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS agency_users (
            id TEXT PRIMARY KEY,
            agency_id TEXT NOT NULL,
            email TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
            role TEXT NOT NULL DEFAULT 'collaborator',
            first_name TEXT,
            last_name TEXT,
            active INTEGER NOT NULL DEFAULT 1,
            last_login_at TEXT,
            created_at TEXT NOT NULL,
            FOREIGN KEY (agency_id) REFERENCES agencies(id)
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS auth_sessions (
            token TEXT PRIMARY KEY,
            user_id TEXT NOT NULL,
            agency_id TEXT NOT NULL,
            created_at TEXT NOT NULL,
            expires_at TEXT NOT NULL
        )
    """)

    acols = {r[1] for r in conn.execute("PRAGMA table_info(agencies)").fetchall()}
    if acols:
        billing_cols = (
            ("stripe_customer_id", "TEXT"),
            ("stripe_subscription_id", "TEXT"),
            ("subscription_status", "TEXT NOT NULL DEFAULT 'active'"),
            ("subscription_current_period_end", "TEXT"),
            ("subscription_plan", "TEXT DEFAULT 'veliora_pro'"),
        )
        for col, typedef in billing_cols:
            if col not in acols:
                conn.execute(f"ALTER TABLE agencies ADD COLUMN {col} {typedef}")

    conn.execute("CREATE INDEX IF NOT EXISTS idx_leads_source_id ON leads(source_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_leads_agency_id ON leads(agency_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_agency_users_agency ON agency_users(agency_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_crawl_jobs_status ON crawl_jobs(status)")


def _migrate_leads_drop_global_url_unique(conn: sqlite3.Connection) -> None:
    """Permet la même URL d'annonce pour plusieurs agences (isolation)."""
    row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='leads'"
    ).fetchone()
    if not row or not row[0]:
        return
    ddl = row[0].upper()
    if "SOURCE_URL" not in ddl or "UNIQUE" not in ddl:
        return
    if "LEADS_MT" in ddl:
        return
    conn.execute("ALTER TABLE leads RENAME TO leads_mt_old")
    conn.execute("""
        CREATE TABLE leads_mt (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            first_name TEXT,
            last_name TEXT,
            phone TEXT,
            email TEXT,
            address TEXT,
            surface REAL,
            price INTEGER,
            transaction_type TEXT DEFAULT 'vente',
            price_period TEXT,
            published_at TEXT,
            source TEXT,
            source_id TEXT,
            source_url TEXT NOT NULL,
            status TEXT DEFAULT 'nouveau',
            pipeline TEXT DEFAULT 'nouveau',
            listing_type TEXT DEFAULT 'particulier',
            type TEXT DEFAULT 'particulier',
            agency TEXT,
            agency_id TEXT,
            score INTEGER DEFAULT 0,
            missing_fields TEXT DEFAULT '[]',
            created_at TEXT NOT NULL,
            updated_at TEXT
        )
    """)
    old_cols = {r[1] for r in conn.execute("PRAGMA table_info(leads_mt_old)").fetchall()}
    target_cols = [r[1] for r in conn.execute("PRAGMA table_info(leads_mt)").fetchall()]
    shared = [c for c in target_cols if c in old_cols]
    sel = ", ".join(shared)
    conn.execute(f"INSERT INTO leads_mt ({sel}) SELECT {sel} FROM leads_mt_old")
    conn.execute("DROP TABLE leads_mt_old")
    conn.execute("ALTER TABLE leads_mt RENAME TO leads")


def _init_sqlite() -> None:
    with get_connection() as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS leads (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                first_name TEXT,
                last_name TEXT,
                phone TEXT,
                email TEXT,
                address TEXT,
                surface REAL,
                price INTEGER,
                source TEXT,
                source_url TEXT NOT NULL,
                status TEXT DEFAULT 'nouveau',
                pipeline TEXT DEFAULT 'nouveau',
                type TEXT DEFAULT 'particulier',
                agency TEXT,
                score INTEGER DEFAULT 0,
                missing_fields TEXT DEFAULT '[]',
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS sources (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                base_url TEXT NOT NULL,
                search_url TEXT,
                enabled INTEGER DEFAULT 1,
                found_total INTEGER DEFAULT 0,
                found_today INTEGER DEFAULT 0,
                last_scan TEXT,
                last_error TEXT
            );

            CREATE TABLE IF NOT EXISTS activities (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                type TEXT NOT NULL,
                text TEXT NOT NULL,
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS crawl_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                source_id TEXT,
                url TEXT,
                status TEXT,
                message TEXT,
                created_at TEXT NOT NULL
            );
        """)

        _migrate(conn)
        from crm.mandates.storage import ensure_mandate_tables
        from crm.portal.storage import ensure_portal_tables

        ensure_mandate_tables(conn)
        ensure_portal_tables(conn)
        ensure_leads_performance_indexes(conn)
        conn.commit()


def ensure_leads_performance_indexes(conn) -> None:
    """Index garantissant un chargement rapide de la liste CRM.

    Idempotent (``IF NOT EXISTS``) et appliqué à chaque démarrage : ainsi les
    bases prod existantes (où le schéma complet n'est pas rejoué) bénéficient
    aussi du tri indexé sur ``created_at`` utilisé par ``get_leads``.
    """
    try:
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_leads_created_at "
            "ON leads(created_at DESC)"
        )
        conn.commit()
    except Exception:
        logger.debug("ensure_leads_performance_indexes ignoré", exc_info=True)


def init_db() -> None:
    """Initialise SQLite (local) ou Supabase PostgreSQL (DATABASE_URL)."""
    if is_postgres():
        from velora_db.connection import init_postgres_schema
        from crm.mandates.storage import ensure_mandate_tables
        from crm.portal.storage import ensure_portal_tables

        if os.getenv("VELIORA_AUTO_SCHEMA", "").lower() in ("1", "true", "yes"):
            init_postgres_schema()
        with get_connection() as conn:
            ensure_mandate_tables(conn)
            ensure_portal_tables(conn)
            ensure_leads_performance_indexes(conn)
            try:
                from velora_db.introspect import ensure_columns

                ensure_columns(conn, "leads", {"relisted_at": "TEXT"})
            except Exception:
                logger.exception("ensure leads.relisted_at (postgres)")

        def _deferred_postgres_init() -> None:
            try:
                from crm.maps.service import ensure_map_schema
                from crm.leads.images import ensure_lead_image_schema

                ensure_map_schema()
                ensure_lead_image_schema()
            except Exception:
                logger.exception("ensure_map_schema / lead images (deferred)")
            if os.getenv("VELIORA_AUTO_RLS", "1").strip().lower() not in (
                "0",
                "false",
                "no",
                "",
            ):
                try:
                    from velora_db.connection import secure_public_schema_rls

                    secure_public_schema_rls()
                except Exception:
                    logger.exception("secure_public_schema_rls (deferred)")
            try:
                prune_crawl_logs()
            except Exception:
                logger.debug("prune_crawl_logs (deferred) ignoré", exc_info=True)

        threading.Thread(
            target=_deferred_postgres_init,
            name="veliora-pg-deferred-init",
            daemon=True,
        ).start()
        logger.info("Base Supabase Veliora prête (init lourde en arrière-plan)")
        return
    _init_sqlite()
    try:
        from crm.maps.service import ensure_map_schema
        from crm.leads.images import ensure_lead_image_schema

        ensure_map_schema()
        ensure_lead_image_schema()
    except Exception:
        logger.exception("ensure_map_schema / lead images")


def scoped_source_id(agency_id: str, base_id: str) -> str:
    return f"{agency_id}_{base_id}"


def seed_default_sources_for_agency(agency_id: str) -> int:
    """Portails par défaut — ajoute les manquants et met à jour URLs de référence."""
    return sync_default_sources_for_agency(agency_id)


def sync_default_sources_for_agency(agency_id: str) -> int:
    """Portails Veliora par agence : présents pour tout le monde, URLs à jour."""
    from crm.leads.shared_pool import is_shared_pool_agency_id

    if is_shared_pool_agency_id(agency_id):
        return 0

    now = _now()
    touched = 0
    with get_connection() as conn:
        for cfg in DEFAULT_SOURCES:
            sid = scoped_source_id(agency_id, cfg.id)
            row = conn.execute(
                "SELECT id FROM sources WHERE id = ? AND agency_id = ?",
                (sid, agency_id),
            ).fetchone()
            enabled = 1 if getattr(cfg, "enabled", True) else 0
            if row:
                conn.execute(
                    """UPDATE sources SET name = ?, base_url = ?, search_url = ?,
                       enabled = ?, is_custom = 0, updated_at = ?
                       WHERE id = ? AND agency_id = ?""",
                    (cfg.name, cfg.base_url, cfg.search_url, enabled, now, sid, agency_id),
                )
            else:
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
                    (sid, cfg.name, cfg.base_url, cfg.search_url, enabled, agency_id, now, now),
                )
            touched += 1
        conn.commit()
    return touched


def is_default_portal_source(source_id: str) -> bool:
    from crawler.portals import resolve_base_portal_id

    return resolve_base_portal_id(source_id) is not None


def is_protected_portal_source(src: dict) -> bool:
    """Portail anti-bot (PAP, LBC…) — indépendant de l'activation crawl."""
    from crawler.portals import COMING_SOON_PORTAL_IDS, resolve_base_portal_id

    base = resolve_base_portal_id(src.get("id") or "")
    return bool(base and base in COMING_SOON_PORTAL_IDS)


def is_antibot_source(src: dict) -> bool:
    """Portail protégé — « Bientôt disponible » seulement si le crawl navigateur est inactif."""
    from crawler.config import antibot_portals_crawl_enabled

    if antibot_portals_crawl_enabled():
        return False
    return is_protected_portal_source(src)


# Gros portails à crawler EN PRIORITÉ avec Decodo (proxies résidentiels), dans cet
# ordre. Quand le crawl anti-bot est actif (navigateur Playwright + Decodo), ils
# passent DEVANT les portails « gratuits » : ce sont les plus riches en annonces de
# particuliers, donc on veut leurs résultats en premier. Tant que Decodo/Playwright
# est inactif, `is_antibot_source` les classe « Bientôt » et ils retombent plus bas.
PRIORITY_PORTAL_ORDER = ("seloger", "pap", "bienici", "leboncoin", "logicimmo")


def _priority_portal_rank(src: dict) -> int | None:
    """Rang du portail dans PRIORITY_PORTAL_ORDER (0 = crawlé en premier), sinon None."""
    from crawler.portals import resolve_base_portal_id

    base = resolve_base_portal_id(src.get("id") or "")
    if base and base in PRIORITY_PORTAL_ORDER:
        return PRIORITY_PORTAL_ORDER.index(base)
    return None


def _source_sort_key(src: dict) -> tuple:
    """Ordre de crawl : gros portails (Decodo) → recommandés → anti-bot off → perso.

    Tier 0 : gros portail anti-bot crawlable maintenant (navigateur + Decodo actifs),
    placé en tête dans l'ordre de PRIORITY_PORTAL_ORDER (SeLoger, PAP, Bien'ici…).
    Tier 1 : autres portails recommandés (ParuVendu, Ouest-France Immo…).
    Tier 2 : anti-bot pas encore activé (« Bientôt disponible »).
    Tier 3 : sites personnalisés ajoutés par l'agence.
    """
    name = src.get("name") or ""
    if src.get("is_custom"):
        return (3, 99, name)
    if is_antibot_source(src):
        # Anti-bot mais crawl navigateur/Decodo inactif → après les portails actifs.
        return (2, 99, name)
    rank = _priority_portal_rank(src)
    if rank is not None:
        # Gros portail désormais crawlable (Decodo) → tout en haut, ordre prioritaire.
        return (0, rank, name)
    return (1, 99, name)


# Portails payants — pas de préchauffage navigateur en crawl gratuit.
HARD_ANTIBOT_HOSTS = tuple(
    pid.replace("logicimmo", "logic-immo") for pid in (
        "leboncoin", "pap", "seloger", "bienici", "logicimmo"
    )
)


def _crawl_priority(src: dict) -> int:
    blob = f"{src.get('id', '')} {src.get('base_url', '')} {src.get('search_url', '')}".lower()
    return 1 if any(h in blob for h in HARD_ANTIBOT_HOSTS) else 0


def find_streamestate_source(agency_id: str) -> dict | None:
    """Source « Analyse approfondie » (recrawl Decodo) de l'agence."""
    from crawler.portals import resolve_base_portal_id

    for s in get_sources(agency_id, sync=False, live_counts=False):
        if resolve_base_portal_id(s.get("id") or "") == "streamestate":
            return s
    return None


def is_streamestate_enabled_for_agency(agency_id: str) -> bool:
    """True si l'analyse approfondie est activée dans les portails de l'agence."""
    from crawler.config import CRAWL_SKIP_STREAMESTATE

    if CRAWL_SKIP_STREAMESTATE:
        return False
    src = find_streamestate_source(agency_id)
    return bool(src and src.get("enabled"))


def is_recommended_crawl_source(src: dict) -> bool:
    """Portail ou site catalogue — inclus dans « Crawler tout » (hors anti-bot et perso)."""
    if not src.get("enabled"):
        return False
    if src.get("is_custom"):
        return False
    if is_antibot_source(src):
        return False
    url = (src.get("search_url") or src.get("base_url") or "").strip()
    if not url.startswith("http"):
        return False
    from crawler.immobilier_catalog import resolve_catalog_id
    from crawler.portals import resolve_base_portal_id

    if resolve_catalog_id(src.get("id") or ""):
        from crawler.config import CRAWL_INCLUDE_CATALOG_IN_AUTO

        return CRAWL_INCLUDE_CATALOG_IN_AUTO
    base = resolve_base_portal_id(src.get("id") or "")
    if base == "streamestate":
        from crawler.config import CRAWL_SKIP_STREAMESTATE, STREAMESTATE_INCLUDE_IN_VEILLE
        from crawler.deep_analysis import deep_analysis_configured

        if CRAWL_SKIP_STREAMESTATE:
            return False
        return deep_analysis_configured() and STREAMESTATE_INCLUDE_IN_VEILLE
    return base is not None


def get_sources_for_full_crawl(agency_id: str) -> list[dict]:
    """Portails + réseaux agences + petites annonces pour la veille auto."""
    from crm.leads.shared_pool import is_shared_pool_agency_id
    from crawler.config import (
        CRAWL_INCLUDE_CATALOG_IN_AUTO,
        CRAWL_INCLUDE_CUSTOM_IN_AUTO,
        antibot_portals_crawl_enabled,
    )
    from crawler.immobilier_catalog import sync_immobilier_catalog_for_agency
    from crawler.portals import resolve_base_portal_id

    if is_shared_pool_agency_id(agency_id) or str(agency_id or "").strip().lower() == "none":
        return []
    seed_default_sources_for_agency(agency_id)
    if CRAWL_INCLUDE_CATALOG_IN_AUTO:
        sync_immobilier_catalog_for_agency(agency_id)
    sources: list[dict] = []
    seen: set[str] = set()
    for s in get_sources(agency_id):
        sid = s.get("id") or ""
        if sid in seen or not s.get("enabled"):
            continue
        if is_recommended_crawl_source(s):
            sources.append(s)
            seen.add(sid)
            continue
        if (
            antibot_portals_crawl_enabled()
            and resolve_base_portal_id(sid)
            and (s.get("search_url") or s.get("base_url") or "").startswith("http")
        ):
            sources.append(s)
            seen.add(sid)
            continue
        if not CRAWL_INCLUDE_CUSTOM_IN_AUTO:
            continue
        if not s.get("is_custom"):
            continue
        if is_antibot_source(s):
            continue
        url = (s.get("search_url") or s.get("base_url") or "").strip()
        if url.startswith("http"):
            sources.append(s)
            seen.add(sid)
    return sorted(sources, key=_source_sort_key)


def get_leads_stale_for_refresh(
    agency_id: str,
    *,
    limit: int = 15,
    stale_hours: int = 24,
) -> list[dict]:
    """Prospects actifs avec URL, non rafraîchis depuis stale_hours (les plus anciens d'abord)."""
    limit = max(1, int(limit))
    hours = max(1, int(stale_hours))
    # On sur-échantillonne les candidats puis on applique le MÊME filtre de
    # visibilité que get_lead (territoire / villes de l'agence). Sans ça, des fiches
    # hors secteur encore rattachées à l'agence (ex. après changement de villes
    # cibles) étaient renvoyées en boucle puis jugées « Prospect introuvable » au
    # refresh → logs bruyants + créneaux de rafraîchissement gaspillés.
    candidate_limit = max(limit * 5, 50)
    # Intervalle en littéral pour sql_adapt Postgres (datetime('now', ?) non traduit).
    with get_connection() as conn:
        rows = conn.execute(
            f"""SELECT * FROM leads
               WHERE agency_id = ?
                 AND COALESCE(status, 'nouveau') != 'retire'
                 AND TRIM(COALESCE(source_url, '')) != ''
                 AND COALESCE(updated_at, created_at)
                     < datetime('now', '-{hours} hours')
               ORDER BY COALESCE(updated_at, created_at) ASC
               LIMIT ?""",
            (agency_id, candidate_limit),
        ).fetchall()
    from crm.leads.shared_pool import lead_visible_to_agency

    out: list[dict] = []
    for r in rows:
        d = dict(r)
        if lead_visible_to_agency(d, agency_id):
            out.append(d)
            if len(out) >= limit:
                break
    return out


# ─── Crawl jobs ───

# Cache mémoire pour le polling UI (évite une requête SQL toutes les 3 s pendant un crawl).
_crawl_job_cache: dict[tuple[str, str], dict] = {}
_crawl_job_cache_lock = threading.Lock()
_CRAWL_JOB_CACHE_TTL_SEC = float(os.getenv("CRAWL_JOB_CACHE_TTL_SEC", "90"))
_last_expire_stale_at = 0.0
_EXPIRE_STALE_MIN_INTERVAL_SEC = float(os.getenv("CRAWL_EXPIRE_STALE_INTERVAL_SEC", "45"))


def _crawl_job_cache_key(job_id: str, agency_id: str) -> tuple[str, str]:
    return (job_id, agency_id)


def _touch_crawl_job_cache(job: dict | None) -> None:
    if not job or not job.get("id"):
        return
    aid = (job.get("agency_id") or "").strip()
    if not aid:
        return
    with _crawl_job_cache_lock:
        _crawl_job_cache[_crawl_job_cache_key(job["id"], aid)] = {
            "job": copy.deepcopy(job),
            "ts": time.monotonic(),
        }


def _merge_crawl_job_cache(job_id: str, fields: dict) -> None:
    with _crawl_job_cache_lock:
        for (jid, _aid), entry in _crawl_job_cache.items():
            if jid != job_id:
                continue
            row = entry["job"]
            for key, val in fields.items():
                row[key] = val
            entry["ts"] = time.monotonic()


def peek_crawl_job_for_poll(job_id: str, agency_id: str) -> dict | None:
    """État du job depuis le cache (sans SQL) — utilisé par le polling « lite »."""
    with _crawl_job_cache_lock:
        entry = _crawl_job_cache.get(_crawl_job_cache_key(job_id, agency_id))
    if not entry:
        return None
    if time.monotonic() - entry["ts"] > _CRAWL_JOB_CACHE_TTL_SEC:
        return None
    return copy.deepcopy(entry["job"])


def maybe_expire_stale_crawl_jobs() -> int:
    """expire_stale_crawl_jobs au plus une fois toutes les N secondes (évite la saturation pool)."""
    global _last_expire_stale_at
    now = time.monotonic()
    if now - _last_expire_stale_at < _EXPIRE_STALE_MIN_INTERVAL_SEC:
        return 0
    _last_expire_stale_at = now
    return expire_stale_crawl_jobs()


def create_crawl_job(
    job_type: str,
    target_url: str,
    source_id: str | None = None,
    *,
    agency_id: str | None = None,
    city: str | None = None,
    eta_seconds: int | None = None,
    listings_total: int | None = None,
) -> dict:
    job_id = str(uuid.uuid4())
    with get_connection() as conn:
        conn.execute(
            """INSERT INTO crawl_jobs
               (id, source_id, target_url, job_type, status, progress, message,
                city, eta_seconds, listings_total, listings_done, agency_id, created_at)
               VALUES (?, ?, ?, ?, 'pending', 0, 'En attente…', ?, ?, ?, 0, ?, ?)""",
            (
                job_id,
                source_id,
                target_url,
                job_type,
                (city or "").strip() or None,
                eta_seconds,
                listings_total,
                agency_id,
                _now(),
            ),
        )
        conn.commit()
    return get_crawl_job(job_id, agency_id=agency_id)


def update_crawl_job(job_id: str, **fields) -> None:
    allowed = {
        "status", "progress", "leads_found", "leads_saved", "leads_updated",
        "errors", "warnings", "message", "started_at", "finished_at",
        "eta_seconds", "listings_total", "listings_done",
    }
    updates = {k: v for k, v in fields.items() if k in allowed}
    if not updates:
        return

    sets = []
    values = []
    for key, val in updates.items():
        if key in ("errors", "warnings"):
            val = json.dumps(val, ensure_ascii=False)
        sets.append(f"{key} = ?")
        values.append(val)

    values.append(job_id)
    with get_connection() as conn:
        conn.execute(f"UPDATE crawl_jobs SET {', '.join(sets)} WHERE id = ?", values)
        conn.commit()
    # Mise à jour cache pour les polls lite sans relire la base.
    raw_fields = {k: v for k, v in fields.items() if k in allowed}
    if raw_fields:
        _merge_crawl_job_cache(job_id, raw_fields)


def get_crawl_job(job_id: str, agency_id: str | None = None) -> dict | None:
    with get_connection() as conn:
        if agency_id:
            row = conn.execute(
                "SELECT * FROM crawl_jobs WHERE id = ? AND agency_id = ?",
                (job_id, agency_id),
            ).fetchone()
        else:
            row = conn.execute("SELECT * FROM crawl_jobs WHERE id = ?", (job_id,)).fetchone()
        job = _row_to_job(row) if row else None
    if job:
        _touch_crawl_job_cache(job)
    return job


def mark_crawl_jobs_interrupted_on_startup() -> int:
    """Après arrêt brutal (Ctrl+C, kill du port) : libère les jobs bloqués en « running »."""
    now = _now()
    with get_connection() as conn:
        cur = conn.execute(
            """UPDATE crawl_jobs SET status = 'failed', progress = 0,
               message = 'Interrompu — serveur arrêté ou redémarré',
               finished_at = ?
               WHERE status IN ('pending', 'running')""",
            (now,),
        )
        conn.commit()
        return cur.rowcount or 0


def expire_stale_crawl_jobs() -> int:
    """Marque comme échoués les jobs bloqués (serveur redémarré, crawl jamais lancé)."""
    now = _now()
    with get_connection() as conn:
        cur1 = conn.execute(
            """UPDATE crawl_jobs SET status = 'failed', progress = 0,
               message = 'Annulé — en attente trop longtemps (aucun crawl réel)',
               finished_at = ?
               WHERE status = 'pending'
               AND datetime(created_at) < datetime('now', '-90 seconds')""",
            (now,),
        )
        cur2 = conn.execute(
            """UPDATE crawl_jobs SET status = 'failed',
               message = 'Interrompu — crawl bloqué ou trop long (timeout)',
               finished_at = ?
               WHERE status = 'running'
               AND (
                 started_at IS NULL
                 OR datetime(started_at) < datetime('now', '-90 minutes')
               )""",
            (now,),
        )
        conn.commit()
        return (cur1.rowcount or 0) + (cur2.rowcount or 0)


def cancel_crawl_job(job_id: str, agency_id: str) -> bool:
    with get_connection() as conn:
        row = conn.execute(
            "SELECT id, status FROM crawl_jobs WHERE id = ? AND agency_id = ?",
            (job_id, agency_id),
        ).fetchone()
        if not row or row["status"] not in ("pending", "running"):
            return False
        # Le message est passé en paramètre — sinon l'apostrophe française de
        # « l'utilisateur » casse la syntaxe SQL sur Postgres (et plante en prod).
        conn.execute(
            """UPDATE crawl_jobs SET status = 'failed',
               message = ?, finished_at = ? WHERE id = ? AND agency_id = ?""",
            ("Annulé par l'utilisateur", _now(), job_id, agency_id),
        )
        conn.commit()
    return True


def crawl_job_should_stop(job_id: str | None) -> bool:
    """True si le job a été annulé ou n'est plus actif (pending/running)."""
    if not job_id:
        return False
    with get_connection() as conn:
        row = conn.execute(
            "SELECT status FROM crawl_jobs WHERE id = ?", (job_id,)
        ).fetchone()
    if not row:
        return True
    return row["status"] not in ("pending", "running")


def cancel_all_active_crawl_jobs(agency_id: str | None = None) -> int:
    expire_stale_crawl_jobs()
    with get_connection() as conn:
        if agency_id:
            cur = conn.execute(
                """UPDATE crawl_jobs SET status = 'failed',
                   message = 'Annulé', finished_at = ?
                   WHERE status IN ('pending', 'running') AND agency_id = ?""",
                (_now(), agency_id),
            )
        else:
            cur = conn.execute(
                """UPDATE crawl_jobs SET status = 'failed',
                   message = 'Annulé', finished_at = ?
                   WHERE status IN ('pending', 'running')""",
                (_now(),),
            )
        conn.commit()
        return cur.rowcount or 0


PORTAL_CRAWL_JOB_TYPES = frozenset(
    {"all_sources", "veille_auto", "single_source", "url", "deep_analysis_verify"}
)
REFRESH_CRAWL_JOB_TYPES = frozenset({"lead_refresh", "listing_import"})


def crawl_job_lane(job_type: str | None) -> str:
    if (job_type or "") in REFRESH_CRAWL_JOB_TYPES:
        return "refresh"
    return "portal"


def get_pending_or_running_crawl_job(
    agency_id: str,
    *,
    lane: str = "any",
) -> dict | None:
    """Job en cours — par file : portal (veille portails) ou refresh (fiches), sans se bloquer."""
    maybe_expire_stale_crawl_jobs()
    types: tuple[str, ...] | None = None
    if lane == "portal":
        types = tuple(PORTAL_CRAWL_JOB_TYPES)
    elif lane == "refresh":
        types = tuple(REFRESH_CRAWL_JOB_TYPES)
    with get_connection() as conn:
        if types:
            placeholders = ",".join("?" for _ in types)
            row = conn.execute(
                f"""SELECT * FROM crawl_jobs
                   WHERE agency_id = ? AND status IN ('pending', 'running')
                     AND job_type IN ({placeholders})
                   ORDER BY created_at DESC LIMIT 1""",
                (agency_id, *types),
            ).fetchone()
        else:
            row = conn.execute(
                """SELECT * FROM crawl_jobs
                   WHERE agency_id = ? AND status IN ('pending', 'running')
                   ORDER BY created_at DESC LIMIT 1""",
                (agency_id,),
            ).fetchone()
    return _row_to_job(row) if row else None


def get_active_crawl_job(agency_id: str | None = None) -> dict | None:
    maybe_expire_stale_crawl_jobs()
    with get_connection() as conn:
        if agency_id:
            row = conn.execute(
                """SELECT * FROM crawl_jobs
                   WHERE status = 'running' AND agency_id = ?
                   AND started_at IS NOT NULL
                   AND datetime(started_at) > datetime('now', '-12 hours')
                   ORDER BY created_at DESC LIMIT 1""",
                (agency_id,),
            ).fetchone()
        else:
            row = conn.execute(
                """SELECT * FROM crawl_jobs
                   WHERE status = 'running'
                   AND started_at IS NOT NULL
                   AND datetime(started_at) > datetime('now', '-12 hours')
                   ORDER BY created_at DESC LIMIT 1"""
            ).fetchone()
        return _row_to_job(row) if row else None


def get_last_crawl_job(agency_id: str) -> dict | None:
    """Dernier job terminé (ou échoué) — pour afficher si la veille produit des résultats."""
    with get_connection() as conn:
        row = conn.execute(
            """SELECT * FROM crawl_jobs
               WHERE agency_id = ?
                 AND status IN ('completed', 'failed', 'cancelled')
               ORDER BY COALESCE(finished_at, created_at) DESC
               LIMIT 1""",
            (agency_id,),
        ).fetchone()
    return _row_to_job(row) if row else None


def crawl_veille_readiness(agency_id: str) -> dict:
    """Prérequis veille auto : ville, portails, dernier résultat."""
    from crawler.config import (
        CRAWL_AUTO_FREE_PROXIES,
        CRAWL_PLAYWRIGHT_ENABLED,
        CRAWL_PROXIES,
        antibot_portals_crawl_enabled,
        proxies_enabled,
    )

    city = (get_agency_primary_city(agency_id) or "").strip()
    portails = get_sources_for_full_crawl(agency_id)
    last = get_last_crawl_job(agency_id)
    blockers: list[str] = []
    hints: list[str] = []
    if not city:
        hints.append(
            "Aucune ville territoire : crawl national (toute la France) — "
            "renseignez une ville pour filtrer localement."
        )
    if not portails:
        blockers.append("Aucun portail recommandé activé avec une URL de recherche")

    antibot_on = antibot_portals_crawl_enabled()
    proxies_on = proxies_enabled()
    if not antibot_on:
        hints.append(
            "Leboncoin, PAP, SeLoger, Bien’ici : exclus tant que le pool IP auto est vide "
            "(chargement au démarrage) — ajoutez CRAWL_PROXIES pour plus de fiabilité."
        )
    elif proxies_on and CRAWL_PROXIES:
        hints.append("Rotation IP : vos proxies CRAWL_PROXIES (prioritaires).")
    elif CRAWL_AUTO_FREE_PROXIES:
        hints.append(
            "Rotation IP automatique active (pool public testé) + portails protégés inclus."
        )
    elif CRAWL_PLAYWRIGHT_ENABLED:
        hints.append(
            "Portails protégés activés sans proxy : risque de blocage rapide sur Scalingo."
        )

    return {
        "city": city or None,
        "portails_veille_count": len(portails),
        "portails_veille_names": [p.get("name") for p in portails[:6]],
        "blockers": blockers,
        "hints": hints,
        "ready": not blockers,
        "antibot_portals_in_veille": antibot_on,
        "proxies_configured": proxies_on,
        "last_job": last,
    }


def _ensure_crawl_lead_changes_table(conn) -> None:
    conn.execute("""
        CREATE TABLE IF NOT EXISTS crawl_lead_changes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            job_id TEXT NOT NULL,
            agency_id TEXT NOT NULL,
            lead_id INTEGER NOT NULL,
            change_type TEXT NOT NULL,
            summary TEXT NOT NULL,
            details_json TEXT NOT NULL DEFAULT '[]',
            source_name TEXT,
            listing_url TEXT,
            owner_label TEXT,
            created_at TEXT NOT NULL
        )
    """)
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_crawl_lead_changes_agency "
        "ON crawl_lead_changes(agency_id, created_at DESC)"
    )


def insert_crawl_lead_change(
    *,
    job_id: str,
    agency_id: str,
    lead_id: int,
    change_type: str,
    summary: str,
    details: list[str],
    source_name: str | None,
    listing_url: str | None,
    owner_label: str | None,
) -> None:
    with get_connection() as conn:
        _ensure_crawl_lead_changes_table(conn)
        conn.execute(
            """INSERT INTO crawl_lead_changes
               (job_id, agency_id, lead_id, change_type, summary, details_json,
                source_name, listing_url, owner_label, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                job_id,
                agency_id,
                lead_id,
                change_type,
                summary,
                json.dumps(details, ensure_ascii=False),
                source_name,
                listing_url,
                owner_label,
                _now(),
            ),
        )
        conn.commit()


def get_veille_feed(agency_id: str, limit: int = 40) -> list[dict]:
    """Derniers ajouts / mises à jour pour le panneau veille CRM."""
    limit = max(1, min(int(limit), 100))
    with get_connection() as conn:
        _ensure_crawl_lead_changes_table(conn)
        rows = conn.execute(
            """SELECT c.*, l.mandate_score, l.city AS lead_city
               FROM crawl_lead_changes c
               LEFT JOIN leads l ON l.id = c.lead_id AND l.agency_id = c.agency_id
               WHERE c.agency_id = ?
               ORDER BY c.created_at DESC
               LIMIT ?""",
            (agency_id, limit),
        ).fetchall()
    out: list[dict] = []
    for r in rows:
        d = dict(r)
        try:
            d["details"] = json.loads(d.pop("details_json", "[]") or "[]")
        except (json.JSONDecodeError, TypeError):
            d["details"] = []
        out.append(d)
    return out


def _json_job_field(raw, default=None):
    default = default if default is not None else []
    if raw is None or raw == "":
        return default
    if isinstance(raw, (list, dict)):
        return raw
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return default


def _row_to_job(row: sqlite3.Row) -> dict:
    keys = row.keys()
    return {
        "id": row["id"],
        "source_id": row["source_id"],
        "target_url": row["target_url"],
        "job_type": row["job_type"],
        "status": row["status"],
        "progress": row["progress"],
        "leads_found": row["leads_found"],
        "leads_saved": row["leads_saved"],
        "leads_updated": row["leads_updated"] if "leads_updated" in keys else 0,
        "errors": _json_job_field(row["errors"] if "errors" in keys else None),
        "warnings": _json_job_field(row["warnings"] if "warnings" in keys else None),
        "message": row["message"],
        "started_at": row["started_at"],
        "finished_at": row["finished_at"],
        "created_at": row["created_at"],
        "city": row["city"] if "city" in keys else None,
        "eta_seconds": row["eta_seconds"] if "eta_seconds" in keys else None,
        "listings_total": row["listings_total"] if "listings_total" in keys else None,
        "listings_done": row["listings_done"] if "listings_done" in keys else 0,
        "agency_id": row["agency_id"] if "agency_id" in keys else None,
    }


# ─── Leads ───

def reactivate_lead_after_repair(lead_id: int, agency_id: str) -> None:
    """Remet une fiche retirée dans le radar après réparation réussie."""
    with get_connection() as conn:
        conn.execute(
            """UPDATE leads SET
               status = 'nouveau',
               pipeline = 'nouveau',
               updated_at = ?
               WHERE id = ? AND agency_id = ? AND status = 'retire'""",
            (_now(), lead_id, agency_id),
        )
        conn.commit()


def withdraw_lead_incoherent(
    lead_id: int,
    agency_id: str,
    *,
    reason: str,
    source_id: str | None = None,
    job_id: str | None = None,
) -> bool:
    """Retire une fiche incohérente du radar (sans suppression en base)."""
    reason = (reason or "annonce incohérente").strip()[:500]
    with get_connection() as conn:
        row = conn.execute(
            "SELECT id, notes, first_name, last_name, address FROM leads WHERE id = ? AND agency_id = ?",
            (lead_id, agency_id),
        ).fetchone()
        if not row:
            return False
        stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M")
        note_line = f"[{stamp}] Retiré du radar — {reason}"
        prev_notes = (row["notes"] or "").strip()
        notes = f"{prev_notes}\n{note_line}".strip() if prev_notes else note_line
        missing_json = json.dumps(["incohérent"], ensure_ascii=False)
        conn.execute(
            """UPDATE leads SET
               status = 'retire',
               pipeline = 'perdu',
               score = 0,
               mandate_score = 0,
               mandate_score_reason = ?,
               missing_fields = ?,
               notes = ?,
               updated_at = ?
               WHERE id = ? AND agency_id = ?""",
            (reason[:200], missing_json, notes, _now(), lead_id, agency_id),
        )
        conn.commit()
    owner_label = " ".join(p for p in (row["first_name"], row["last_name"]) if p)
    label = (row["address"] or owner_label or f"#{lead_id}")[:80]
    add_activity(
        "crawl",
        f"Fiche retirée (incohérente) — {label}",
        agency_id,
    )
    if source_id:
        add_crawl_log(
            source_id,
            "",
            "withdrawn",
            f"Retiré après vérification — {reason[:120]}",
            job_id,
        )
    return True


def retire_lead_after_sale(lead_id: int, agency_id: str) -> bool:
    """Retire une fiche du radar Prospects après clôture transaction (vendu/loué)."""
    lead = get_lead(lead_id, agency_id)
    if not lead:
        return False
    reason = "Transaction finalisée — bien vendu ou loué"
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M")
    note_line = f"[{stamp}] {reason}"
    prev_notes = (lead.get("notes") or "").strip()
    notes = f"{prev_notes}\n{note_line}".strip() if prev_notes else note_line
    with get_connection() as conn:
        conn.execute(
            """UPDATE leads SET
               status = 'retire',
               pipeline = 'vendu',
               notes = ?,
               updated_at = ?
               WHERE id = ?""",
            (notes, _now(), lead_id),
        )
        conn.commit()
    label = (lead.get("address") or lead.get("owner") or f"#{lead_id}")[:80]
    add_activity("contact", f"Affaire conclue — {label} retiré des prospects", agency_id)
    return True


def get_lead_by_source_url(source_url: str, agency_id: str | None = None) -> dict | None:
    with get_connection() as conn:
        if agency_id:
            row = conn.execute(
                "SELECT * FROM leads WHERE source_url = ? AND agency_id = ?",
                (source_url, agency_id),
            ).fetchone()
        else:
            row = conn.execute(
                "SELECT * FROM leads WHERE source_url = ?", (source_url,)
            ).fetchone()
        return _row_to_lead(row) if row else None


def _segment_crawled_lead(preview: dict, lead: LeadData, partial: bool) -> dict:
    """Pipeline et typage automatique à l'enregistrement crawl."""
    score = int(preview.get("mandate_score") or 0)
    listing_type = lead.type or "particulier"
    transaction = lead.transaction_type or "vente"
    pipeline = "nouveau"
    status = "nouveau"

    if listing_type == "agence":
        pipeline = "nouveau"
    elif score >= 85:
        pipeline = "a_contacter"
    elif score >= 65:
        pipeline = "a_contacter"
    elif partial:
        pipeline = "nouveau"

    return {
        "pipeline": pipeline,
        "status": status,
        "listing_type": listing_type,
        "transaction_type": transaction,
    }


def claim_orphan_leads(agency_id: str) -> int:
    """Rattache les prospects orphelins liés aux sources de l'agence (pas tous les orphelins globaux)."""
    if not agency_id:
        return 0
    if os.getenv("DISABLE_CLAIM_ORPHAN_LEADS", "").strip().lower() in ("1", "true", "yes"):
        return 0
    with get_connection() as conn:
        orphan = conn.execute(
            """SELECT 1 FROM leads
               WHERE (agency_id IS NULL OR agency_id = '')
                 AND source_id IN (SELECT id FROM sources WHERE agency_id = ?)
               LIMIT 1""",
            (agency_id,),
        ).fetchone()
        if not orphan:
            return 0
        cur = conn.execute(
            """UPDATE leads SET agency_id = ?, updated_at = ?
               WHERE (agency_id IS NULL OR agency_id = '')
                 AND source_id IN (SELECT id FROM sources WHERE agency_id = ?)""",
            (agency_id, _now(), agency_id),
        )
        conn.commit()
        n = cur.rowcount
    if n:
        recalc_source_found_counts(agency_id)
        add_activity("crawl", f"{n} prospect(s) rattaché(s) à votre agence", agency_id)
    return n


def _record_price_change(
    conn,
    lead_id: int,
    agency_id: str | None,
    new_price: int,
    *,
    now: str,
) -> tuple[int, str | None]:
    """Historise un prix et met à jour les compteurs de baisses."""
    # Les leads du pool national ont agency_id NULL ; or lead_price_history.agency_id
    # est NOT NULL et `WHERE agency_id = NULL` ne matche jamais en SQL. On historise
    # donc sous une clé sentinelle stable, et on cible le lead par sa PK (id).
    hist_agency_id = (agency_id or "").strip() or "__shared__"
    conn.execute(
        """INSERT INTO lead_price_history (lead_id, agency_id, price, recorded_at)
           VALUES (?, ?, ?, ?)""",
        (lead_id, hist_agency_id, new_price, now),
    )
    rows = conn.execute(
        """SELECT price, recorded_at FROM lead_price_history
           WHERE lead_id = ? AND agency_id = ? ORDER BY recorded_at ASC""",
        (lead_id, hist_agency_id),
    ).fetchall()
    hist = [{"price": r["price"], "recorded_at": r["recorded_at"]} for r in rows]
    from crm.scoring.price_history import count_price_drops_from_history

    drops, _ = count_price_drops_from_history(hist, current_price=new_price, previous_price=None)
    last_at = now if drops > 0 else None
    conn.execute(
        """UPDATE leads SET price_change_count = ?, last_price_change_at = COALESCE(?, last_price_change_at)
           WHERE id = ?""",
        (drops, last_at, lead_id),
    )
    return drops, last_at


def persist_lead_scores(lead_id: int, agency_id: str, enriched: dict) -> None:
    """Écrit scores et explication JSON en base."""
    expl = enriched.get("score_explanation")
    expl_json = json.dumps(expl, ensure_ascii=False) if isinstance(expl, dict) else (expl or "{}")
    now = _now()
    with get_connection() as conn:
        conn.execute(
            """UPDATE leads SET
               score = ?, mandate_score = ?, mandate_score_reason = ?,
               priority_tier = ?, score_explanation = ?, scores_computed_at = ?,
               price_change_count = COALESCE(?, price_change_count),
               updated_at = ?
               WHERE id = ? AND agency_id = ?""",
            (
                enriched.get("score") or enriched.get("mandate_score") or 0,
                enriched.get("mandate_score") or 0,
                enriched.get("mandate_score_reason") or "",
                enriched.get("priority_tier"),
                expl_json,
                now,
                enriched.get("price_change_count"),
                now,
                lead_id,
                agency_id,
            ),
        )
        conn.commit()


def record_lead_outcome_event(
    lead_id: int,
    agency_id: str,
    outcome_type: str,
    *,
    agent_id: str | None = None,
    notes: str | None = None,
    lead_snapshot: dict | None = None,
) -> None:
    """Enregistre un outcome et calibre les poids agence."""
    from crm.scoring.outcomes import (
        calibrate_agency_weights_from_outcome,
        record_lead_outcome,
    )
    from crm.scoring.recalc import scores_snapshot_from_lead

    snap = scores_snapshot_from_lead(lead_snapshot or {})
    with get_connection() as conn:
        record_lead_outcome(
            conn,
            lead_id=lead_id,
            agency_id=agency_id,
            outcome_type=outcome_type,
            agent_id=agent_id,
            notes=notes,
            scores_snapshot=snap,
        )
        calibrate_agency_weights_from_outcome(conn, agency_id, outcome_type, snap)
        conn.commit()
    invalidate_leads_snapshot(agency_id)


def _coerce_source_id(conn, source_id: str | None, agency_id: str | None) -> str | None:
    """Évite les échecs FK Postgres si la source n'existe pas encore."""
    from crm.leads.shared_pool import is_shared_pool_agency_id

    if not source_id:
        return None
    row = conn.execute("SELECT id FROM sources WHERE id = ?", (source_id,)).fetchone()
    if row:
        return source_id
    if is_shared_pool_agency_id(agency_id):
        logger.warning("source_id inconnu pour INSERT lead — %s (pool partagé)", source_id)
        return None
    sync_default_sources_for_agency(agency_id)
    row = conn.execute(
        "SELECT id FROM sources WHERE id = ? AND agency_id = ?",
        (source_id, agency_id),
    ).fetchone()
    if row:
        return source_id
    logger.warning("source_id inconnu pour INSERT lead — %s (agence %s)", source_id, agency_id)
    return None


def _is_unique_violation(exc: Exception) -> bool:
    """True si l'exception est un conflit de clé unique (SQLite ou Postgres).

    Sert à détecter la course entre deux crawls de la même URL : la ligne a été
    créée entre notre SELECT et notre INSERT. On rejoue alors en mise à jour.
    """
    name = type(exc).__name__.lower()
    if "uniqueviolation" in name or "integrityerror" in name:
        text = f"{exc}".lower()
        return (
            "unique" in text
            or "duplicate key" in text
            or "idx_leads_agency_url" in text
            or "source_url" in text
        )
    return False


def save_lead(
    lead: LeadData,
    source_id: str | None = None,
    job_id: str | None = None,
    *,
    agency_id: str | None = None,
    require_verification: bool = True,
    deep_refresh: bool = False,
    veille_recrawl: bool = False,
    _race_retry: bool = False,
) -> dict | None:
    """Enregistre ou met à jour après vérification obligatoire des données."""
    from crawler.validation import (
        lead_from_db_row,
        merge_lead_for_update,
        prepare_lead_defaults,
        resolve_crawl_verification,
        resolve_published_at,
    )

    if not lead.source_url:
        logger.warning("save_lead ignoré — source_url manquant")
        return None

    discovering_agency_id = agency_id
    from crm.leads.shared_pool import pool_agency_id

    agency_id = pool_agency_id()
    if discovering_agency_id:
        lead.raw_extras.setdefault("discovered_by_agency", discovering_agency_id)

    existing_row = get_lead_by_source_url(lead.source_url, None)
    created = existing_row is None

    if existing_row:
        existing_lead = lead_from_db_row(existing_row)
        lead = merge_lead_for_update(existing_lead, lead, deep_refresh=deep_refresh)

    lead = prepare_lead_defaults(lead)
    from crm.dvf import apply_lead_location_fields
    apply_lead_location_fields(lead)
    _clean_lead_location_fields(lead)
    from crawler.address_quality import scrub_lead_address_for_storage

    scrub_lead_address_for_storage(lead)
    lead.city, lead.postcode = _canonicalize_city_postcode_values(
        getattr(lead, "city", None),
        getattr(lead, "postcode", None),
    )
    from crawler.address_quality import ensure_street_address_from_data, sanitize_lead_commune_fields

    sanitize_lead_commune_fields(lead)
    from crawler.config import ADDRESS_MATCH_DURING_CRAWL

    # Voie via DPE/BAN (sync si pas de file parallèle, sinon la file post-crawl).
    ensure_street_address_from_data(lead, run_full_match=not ADDRESS_MATCH_DURING_CRAWL)
    stored_pub = existing_row.get("published_at") if existing_row else None
    lead.published_at = resolve_published_at(lead.published_at, stored_pub)

    effective_require_verification = require_verification
    if existing_row:
        # Recrawl (tous crawlers) : fusionner et réparer les champs sans bloquer sur la vérif stricte.
        effective_require_verification = False

    verification, partial = resolve_crawl_verification(
        lead, require_verification=effective_require_verification
    )
    if require_verification and not verification.ok:
        if not existing_row:
            snap, _partial = resolve_crawl_verification(lead, require_verification=False)
            if snap.ok:
                verification = snap
                partial = True
        if not verification.ok:
            if existing_row and _persist_recrawl_repairs(lead, existing_row, agency_id):
                return {
                    "id": existing_row["id"],
                    "created": False,
                    "verified": False,
                    "partial": True,
                    "verification": verification.summary(),
                    "errors": verification.errors,
                    "updated": True,
                    "repaired": True,
                }
            return {
                "id": existing_row["id"] if existing_row else None,
                "created": False,
                "verified": False,
                "verification": verification.summary(),
                "errors": verification.errors,
            }

    missing_json = json.dumps(lead.missing_fields() if partial else [])

    now = _now()
    score = verification.score
    previous_price = None
    price_changed = False
    surface_changed = False
    if existing_row:
        old_price = existing_row.get("price") or 0
        new_price = lead.price or 0
        if old_price != new_price and new_price:
            price_changed = True
        if (existing_row.get("surface") or 0) != (lead.surface or 0) and lead.surface:
            surface_changed = True
        if old_price and new_price and new_price < old_price:
            previous_price = old_price
        else:
            previous_price = existing_row.get("previous_price")

    # Annonce retirée (hors vente finalisée) revue en ligne = republiée : vendeur
    # de retour, signal de motivation. On réactive la fiche et on horodate.
    relisted = bool(
        existing_row
        and (existing_row.get("status") == "retire")
        and (existing_row.get("pipeline") != "vendu")
    )
    relisted_at_value = now if relisted else (existing_row or {}).get("relisted_at")

    from crm.scoring.recalc import enrich_lead_scores

    preview = enrich_lead_scores({
        "id": existing_row["id"] if existing_row else None,
        "agency_id": agency_id,
        "type": lead.type,
        "price": lead.price,
        "previous_price": previous_price,
        "price_change_count": (existing_row or {}).get("price_change_count"),
        "published_at": resolve_published_at(lead.published_at, stored_pub),
        "created_at": existing_row.get("created_at") if existing_row else now,
        "transaction_type": lead.transaction_type,
        "surface": lead.surface,
        "phone": lead.phone,
        "email": lead.email,
        "address": lead.address,
        "dvf_verdict": (existing_row or {}).get("dvf_verdict"),
        "dvf_delta_pct": (existing_row or {}).get("dvf_delta_pct"),
        "agency": lead.agency,
        "relisted_at": relisted_at_value,
    })
    mandate_score = preview["mandate_score"]
    mandate_reason = preview["mandate_score_reason"]
    priority_tier = preview.get("priority_tier")
    score_explanation_json = json.dumps(
        preview.get("score_explanation") or {},
        ensure_ascii=False,
    )
    segment = _segment_crawled_lead(preview, lead, partial)

    listing_title = lead.raw_extras.get("listing_title") or None
    listing_image_url = (lead.raw_extras.get("listing_image_url") or "").strip() or None
    facts_audit_json = (
        json.dumps(lead.raw_extras.get("facts_audit"), ensure_ascii=False)
        if lead.raw_extras.get("facts_audit")
        else None
    )

    # Already normalized before verification.
    lead_city = getattr(lead, "city", None)
    lead_postcode = getattr(lead, "postcode", None)
    lead_sector = getattr(lead, "sector", None)

    race_retry = False
    with get_connection() as conn:
        source_id = _coerce_source_id(conn, source_id, discovering_agency_id)
        if existing_row:
            old_p = existing_row.get("price") or 0
            new_p = lead.price or 0
            if new_p and new_p != old_p:
                _record_price_change(
                    conn,
                    int(existing_row["id"]),
                    agency_id,
                    int(new_p),
                    now=now,
                )
                pcc_row = conn.execute(
                    "SELECT price_change_count FROM leads WHERE id = ?",
                    (existing_row["id"],),
                ).fetchone()
                if pcc_row is not None:
                    preview["price_change_count"] = pcc_row["price_change_count"]
                preview = enrich_lead_scores(preview)
                mandate_score = preview["mandate_score"]
                mandate_reason = preview["mandate_score_reason"]
                priority_tier = preview.get("priority_tier")
                score_explanation_json = json.dumps(
                    preview.get("score_explanation") or {},
                    ensure_ascii=False,
                )
            # pipeline, status, notes, champs DVF : conservés sauf recalcul DVF si prix/surface change
            dvf_touch = ""
            loc_changed = False
            if existing_row:
                loc_changed = (
                    (existing_row.get("address") or "").strip().lower()
                    != (lead.address or "").strip().lower()
                    or (existing_row.get("city") or "") != (lead_city or "")
                    or (existing_row.get("postcode") or "") != (lead_postcode or "")
                    or (existing_row.get("sector") or "") != (lead_sector or "")
                )
            if price_changed or surface_changed or loc_changed:
                dvf_touch = (
                    ", dvf_compared_at = NULL, dvf_verdict = NULL, dvf_verdict_label = NULL, "
                    "dvf_delta_pct = NULL, dvf_median_m2 = NULL, dvf_sector = NULL, "
                    "dvf_reference_period = NULL"
                )
            # Adresse/ville/CP modifiés au recrawl → les coordonnées en base sont
            # périmées. On les efface pour forcer un nouveau géocodage, sinon le
            # bien resterait affiché à l'ANCIENNE adresse sur la carte.
            geo_touch = ", latitude = NULL, longitude = NULL" if loc_changed else ""
            conn.execute(
                f"""UPDATE leads SET
                   first_name = ?, last_name = ?, phone = ?, email = ?,
                   address = ?, city = ?, postcode = ?, sector = ?,
                   surface = ?, price = ?, previous_price = ?,
                   transaction_type = ?, price_period = ?, published_at = ?,
                   source = ?, source_id = ?, listing_type = ?, agency = ?,
                   score = ?, mandate_score = ?, mandate_score_reason = ?,
                   priority_tier = ?, score_explanation = ?, scores_computed_at = ?,
                   missing_fields = ?,
                   listing_title = COALESCE(NULLIF(?, ''), listing_title),
                   facts_audit = COALESCE(?, facts_audit),
                   listing_image_url = COALESCE(NULLIF(?, ''), listing_image_url),
                   updated_at = ?{dvf_touch}{geo_touch}
                   WHERE id = ?""",
                (
                    lead.first_name,
                    lead.last_name,
                    lead.phone,
                    lead.email,
                    lead.address,
                    lead_city,
                    lead_postcode,
                    lead_sector,
                    lead.surface,
                    lead.price,
                    previous_price,
                    lead.transaction_type,
                    lead.price_period,
                    resolve_published_at(lead.published_at, stored_pub),
                    lead.source,
                    source_id,
                    segment["listing_type"],
                    lead.agency,
                    score,
                    mandate_score,
                    mandate_reason,
                    priority_tier,
                    score_explanation_json,
                    now,
                    missing_json,
                    listing_title,
                    facts_audit_json,
                    listing_image_url or "",
                    now,
                    existing_row["id"],
                ),
            )
            if relisted:
                # Réactive la fiche republiée et l'horodate (le score inclut déjà
                # le bonus via preview["relisted_at"]).
                new_pipeline = (
                    "nouveau"
                    if (existing_row.get("pipeline") in (None, "", "perdu"))
                    else existing_row.get("pipeline")
                )
                conn.execute(
                    """UPDATE leads SET status = 'nouveau', pipeline = ?,
                       relisted_at = ?, updated_at = ? WHERE id = ?""",
                    (new_pipeline, now, now, existing_row["id"]),
                )
            conn.commit()
            saved_out = {
                "id": existing_row["id"],
                "created": False,
                "verified": True,
                "partial": partial,
                "verification": verification.summary(),
                "updated": True,
                "price_changed": price_changed,
                "surface_changed": surface_changed,
                "relisted": relisted,
            }
            _schedule_lead_image_after_save(
                lead,
                agency_id,
                int(existing_row["id"]),
                saved_out,
                deep_refresh=deep_refresh,
            )
            if discovering_agency_id:
                invalidate_sources_cache(discovering_agency_id)
            # Pool partagé modifié : tout instantané de briefing devient périmé.
            invalidate_leads_snapshot()
            return saved_out

        try:
            cur = conn.execute(
                """INSERT INTO leads
                   (first_name, last_name, phone, email, address, city, postcode, sector,
                    surface, price, transaction_type, price_period, published_at, source, source_id,
                    source_url, listing_type, agency, agency_id, score,
                    mandate_score, mandate_score_reason, priority_tier, score_explanation,
                    scores_computed_at, missing_fields,
                    listing_title, facts_audit, listing_image_url, pipeline, status, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    lead.first_name,
                    lead.last_name,
                    lead.phone,
                    lead.email,
                    lead.address,
                    lead_city,
                    lead_postcode,
                    lead_sector,
                    lead.surface,
                    lead.price,
                    lead.transaction_type,
                    lead.price_period,
                    resolve_published_at(lead.published_at),
                    lead.source,
                    source_id,
                    lead.source_url,
                    segment["listing_type"],
                    lead.agency,
                    agency_id,
                    score,
                    mandate_score,
                    mandate_reason,
                    priority_tier,
                    score_explanation_json,
                    now,
                    missing_json,
                    listing_title,
                    facts_audit_json,
                    listing_image_url,
                    segment["pipeline"],
                    segment["status"],
                    now,
                    now,
                ),
            )
        except Exception as exc:
            if _is_unique_violation(exc) and not _race_retry:
                # Course entre deux crawls : la fiche a été créée entre le SELECT
                # et l'INSERT. On bascule en mise à jour (rejoué HORS connexion pour
                # ne pas saturer le pool) — aucune fiche perdue.
                logger.info(
                    "INSERT en course (URL déjà créée) — bascule en mise à jour : %s",
                    lead.source_url[:80],
                )
                race_retry = True
                cur = None
            else:
                logger.exception(
                    "INSERT lead échoué — %s (agence %s): %s",
                    lead.source_url[:80],
                    agency_id,
                    exc,
                )
                return {
                    "id": None,
                    "created": False,
                    "verified": False,
                    "verification": str(exc)[:200],
                    "errors": [str(exc)[:200]],
                }
        if race_retry:
            # Sortie du with (connexion rendue au pool) puis rejeu en mise à jour.
            pass
        elif not cur:
            return None
        else:
            new_id = cur.lastrowid
            if not new_id:
                logger.error(
                    "INSERT lead sans id retourné — %s (agence %s)",
                    lead.source_url[:80],
                    agency_id,
                )
                return {
                    "id": None,
                    "created": False,
                    "verified": False,
                    "verification": "échec enregistrement base",
                    "errors": ["id prospect non créé"],
                }
            if agency_id and new_id and lead.price:
                _record_price_change(conn, int(new_id), agency_id, int(lead.price), now=now)
            conn.commit()
            saved_out = {
                "id": new_id,
                "created": True,
                "verified": True,
                "partial": partial,
                "verification": verification.summary(),
                "updated": False,
            }
            _schedule_lead_image_after_save(
                lead, agency_id, int(new_id), saved_out, deep_refresh=deep_refresh
            )
            if discovering_agency_id:
                invalidate_sources_cache(discovering_agency_id)
            # Pool partagé modifié : tout instantané de briefing devient périmé.
            invalidate_leads_snapshot()
            return saved_out

    # Hors connexion : course détectée à l'INSERT → rejeu en mise à jour (une fois).
    if race_retry:
        return save_lead(
            lead,
            source_id,
            job_id,
            agency_id=discovering_agency_id,
            require_verification=False,
            deep_refresh=deep_refresh,
            veille_recrawl=veille_recrawl,
            _race_retry=True,
        )
    return None


def _schedule_lead_image_after_save(
    lead: LeadData,
    agency_id: str,
    lead_id: int,
    saved: dict,
    *,
    deep_refresh: bool = False,
) -> None:
    if not saved.get("verified") or not lead_id or not agency_id:
        return
    url = (lead.raw_extras or {}).get("listing_image_url")
    if not url:
        with get_connection() as conn:
            row = conn.execute(
                "SELECT listing_image_url FROM leads WHERE id = ? AND agency_id = ?",
                (lead_id, agency_id),
            ).fetchone()
        if row and row["listing_image_url"]:
            url = str(row["listing_image_url"]).strip()
    if not url:
        return
    try:
        from crm.leads.images import schedule_lead_image_sync

        schedule_lead_image_sync(
            lead_id,
            agency_id,
            url,
            respect_custom=True,
            referer=lead.source_url or None,
            force=deep_refresh,
        )
    except Exception:
        logger.exception("schedule lead image %s", lead_id)
    # Galerie complète (toutes les images de l'annonce) — nettoyage marquages inclus.
    try:
        gallery = (lead.raw_extras or {}).get("listing_image_urls") or []
        if not gallery and url:
            gallery = [url]
        if gallery:
            from crm.leads.images import schedule_lead_gallery_sync

            schedule_lead_gallery_sync(
                lead_id,
                agency_id,
                list(gallery),
                referer=lead.source_url or None,
                force=deep_refresh,
            )
    except Exception:
        logger.exception("schedule lead gallery %s", lead_id)
    try:
        from crm.maps.service import schedule_lead_geocode

        schedule_lead_geocode(
            lead_id,
            agency_id,
            lead.address,
            getattr(lead, "postcode", None),
            getattr(lead, "city", None),
        )
    except Exception:
        logger.exception("schedule lead geocode %s", lead_id)


def add_activity(type_: str, text: str, agency_id: str | None = None) -> None:
    with get_connection() as conn:
        conn.execute(
            "INSERT INTO activities (type, text, agency_id, created_at) VALUES (?, ?, ?, ?)",
            (type_, text, agency_id, _now()),
        )
        conn.commit()


_crawl_log_lock = threading.Lock()
_crawl_log_since_prune = 0
# Plafond de rétention : crawl_logs grossissait sans limite (1 ligne par URL
# crawlée) → poids Postgres inutile (Supabase facture la taille de base).
_CRAWL_LOG_KEEP = int(os.getenv("CRAWL_LOG_KEEP", "5000"))
_CRAWL_LOG_PRUNE_EVERY = 250


def prune_crawl_logs(keep: int = _CRAWL_LOG_KEEP) -> None:
    """Ne conserve que les `keep` lignes les plus récentes de crawl_logs."""
    try:
        with get_connection() as conn:
            conn.execute(
                "DELETE FROM crawl_logs "
                "WHERE id <= (SELECT MAX(id) FROM crawl_logs) - ?",
                (keep,),
            )
            conn.commit()
    except Exception:
        logger.debug("prune_crawl_logs ignoré", exc_info=True)


def add_crawl_log(
    source_id: str | None,
    url: str,
    status: str,
    message: str,
    job_id: str | None = None,
) -> None:
    with get_connection() as conn:
        conn.execute(
            """INSERT INTO crawl_logs (job_id, source_id, url, status, message, created_at)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (job_id, source_id, url, status, message, _now()),
        )
        conn.commit()

    # Purge throttlée (1 fois toutes les N insertions) pour borner la taille.
    global _crawl_log_since_prune
    due = False
    with _crawl_log_lock:
        _crawl_log_since_prune += 1
        if _crawl_log_since_prune >= _CRAWL_LOG_PRUNE_EVERY:
            _crawl_log_since_prune = 0
            due = True
    if due:
        prune_crawl_logs()


def get_crawl_logs_for_job(job_id: str, limit: int = 80) -> list[dict]:
    with get_connection() as conn:
        rows = conn.execute(
            """SELECT id, source_id, url, status, message, created_at
               FROM crawl_logs WHERE job_id = ?
               ORDER BY id ASC LIMIT ?""",
            (job_id, limit),
        ).fetchall()
        return [
            {
                "id": r["id"],
                "source_id": r["source_id"],
                "url": r["url"],
                "status": r["status"],
                "message": r["message"],
                "created_at": r["created_at"],
            }
            for r in rows
        ]


def get_source_lead_urls(
    source_id: str, agency_id: str, *, active_only: bool = True
) -> list[str]:
    """URLs déjà crawlées pour cette source (recrawl systématique)."""
    src = get_source(source_id, agency_id)
    if not src:
        return []
    name = src.get("name") or ""
    url_like = _source_url_like(src.get("domain") or "", src.get("base_url") or "")
    status_clause = " AND COALESCE(status, 'nouveau') != 'retire'" if active_only else ""
    from crm.leads.shared_pool import shared_leads_sql_where

    pool_where = shared_leads_sql_where()
    with get_connection() as conn:
        rows = conn.execute(
            f"""SELECT source_url FROM leads
               WHERE {pool_where}
               AND source_url IS NOT NULL AND source_url != ''
               AND (
                   source_id = ?
                   OR ((source_id IS NULL OR source_id = '') AND LOWER(source) = LOWER(?))
                   OR source_url LIKE ?
               ){status_clause}
               ORDER BY COALESCE(mandate_score, 0) DESC, updated_at ASC""",
            (source_id, name, url_like),
        ).fetchall()
    return [str(r["source_url"]) for r in rows if r["source_url"]]


def repair_source_leads_in_db(source_id: str, agency_id: str) -> int:
    """Corrige en base les champs hub / menu sans re-fetch HTTP."""
    from crawler.validation import lead_from_db_row, prepare_lead_defaults, sanitize_lead

    src = get_source(source_id, agency_id)
    name = (src or {}).get("name") or ""
    fixed = 0
    with get_connection() as conn:
        rows = conn.execute(
            """SELECT * FROM leads
               WHERE agency_id = ?
               AND (source_id = ? OR (source_id IS NULL AND source = ?))""",
            (agency_id, source_id, name),
        ).fetchall()
        for row in rows:
            lead = lead_from_db_row(dict(row))
            before = (
                lead.address,
                lead.city,
                lead.postcode,
                lead.first_name,
                lead.last_name,
                lead.phone,
                lead.email,
            )
            lead = sanitize_lead(lead)
            lead = prepare_lead_defaults(lead)
            from crm.dvf import apply_lead_location_fields

            apply_lead_location_fields(lead)
            _clean_lead_location_fields(lead)
            from crawler.address_quality import sanitize_lead_commune_fields

            lead.city, lead.postcode = _canonicalize_city_postcode_values(
                lead.city, lead.postcode
            )
            sanitize_lead_commune_fields(lead)
            after = (
                lead.address,
                lead.city,
                lead.postcode,
                lead.first_name,
                lead.last_name,
                lead.phone,
                lead.email,
            )
            if before == after:
                continue
            conn.execute(
                """UPDATE leads SET
                   first_name = ?, last_name = ?, phone = ?, email = ?,
                   address = ?, city = ?, postcode = ?, updated_at = ?
                   WHERE id = ? AND agency_id = ?""",
                (
                    lead.first_name,
                    lead.last_name,
                    lead.phone,
                    lead.email,
                    lead.address,
                    lead.city,
                    lead.postcode,
                    _now(),
                    row["id"],
                    agency_id,
                ),
            )
            fixed += 1
        if fixed:
            conn.commit()
    return fixed


def get_leads(
    agency_id: str,
    *,
    enrich: bool = True,
    claim_orphans: bool = False,
    prefer_snapshot: bool = False,
    include_extras: bool | None = None,
) -> list[dict]:
    from crm.leads.shared_pool import filter_leads_for_agency, shared_leads_sql_where

    if include_extras is None:
        include_extras = enrich

    # Réutilise l'instantané récent (briefing radar) si demandé. On renvoie une
    # copie de la liste (mêmes dicts) : les consommateurs radar sont en lecture
    # seule, mais on évite qu'un appelant remplace la liste mise en cache.
    if prefer_snapshot and enrich and not claim_orphans:
        cached = _leads_snapshot.get(agency_id)
        if cached and (time.monotonic() - cached[0]) < _LEADS_SNAPSHOT_TTL_SEC:
            return list(cached[1])

    if claim_orphans:
        claim_orphan_leads(agency_id)
    pool_where = shared_leads_sql_where()
    with get_connection() as conn:
        rows = conn.execute(
            f"""SELECT {_LEADS_LIST_SQL} FROM leads
               WHERE {pool_where} OR agency_id = ?
               ORDER BY created_at DESC""",
            (agency_id,),
        ).fetchall()
        leads = [_row_to_lead(r, enrich_scores=False) for r in rows]
    leads = filter_leads_for_agency(leads, agency_id)
    if enrich and leads:
        from crm.scoring.recalc import hydrate_leads_for_list

        leads = hydrate_leads_for_list(leads, agency_id)
    if include_extras and leads:
        _attach_estimates(leads, agency_id)
        try:
            from crm.transactions.service import attach_transactions

            attach_transactions(leads, agency_id)
        except Exception:
            logger.debug("attach_transactions ignoré", exc_info=True)
        result = _annotate_dedup(leads)
    else:
        result = leads
    if enrich:
        # Alimente l'instantané pour le briefing radar qui suit (réutilisation
        # ~1 s plus tard, sans refaire toute la requête + l'enrichissement).
        _leads_snapshot[agency_id] = (time.monotonic(), result)
    return result


def _safe_json_field(raw) -> object | None:
    if not raw:
        return None
    if isinstance(raw, (dict, list)):
        return raw
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return None


def get_lead(lead_id: int, agency_id: str) -> dict | None:
    from crm.leads.shared_pool import lead_visible_to_agency

    with get_connection() as conn:
        row = conn.execute(
            "SELECT * FROM leads WHERE id = ?",
            (lead_id,),
        ).fetchone()
    if not row:
        return None
    lead = _row_to_lead(row, enrich_scores=False)
    if not lead_visible_to_agency(lead, agency_id):
        return None
    try:
        from crm.scoring.recalc import hydrate_lead_from_stored

        lead = hydrate_lead_from_stored(lead)
    except Exception:
        pass
    try:
        from crm.estimator.storage import get_lead_estimate

        payload, at = get_lead_estimate(lead_id, agency_id)
        if payload:
            lead["price_estimate"] = payload
            lead["price_estimate_at"] = at
    except Exception:
        pass
    return lead


def recalc_source_found_counts(agency_id: str | None = None) -> None:
    """Synchronise found_total / found_today avec le nombre réel de prospects.

    L'UPDATE des sources entre en concurrence avec les UPDATE leads (verrou FK
    leads.source_id → sources) des workers DVF parallèles : on retente sur deadlock
    (façon standard de gérer un deadlock Postgres : la transaction perdante rejoue).
    """
    for attempt in range(4):
        try:
            if agency_id:
                sync_lead_source_ids(agency_id)
            with get_connection() as conn:
                # ORDER BY id : verrouillage dans un ordre constant entre
                # transactions concurrentes → réduit les deadlocks sur « sources ».
                if agency_id:
                    rows = conn.execute(
                        "SELECT * FROM sources WHERE agency_id = ? ORDER BY id", (agency_id,)
                    ).fetchall()
                else:
                    rows = conn.execute("SELECT * FROM sources ORDER BY id").fetchall()
                for src in rows:
                    aid = src["agency_id"] if "agency_id" in src.keys() else agency_id
                    if not aid:
                        continue
                    keys = src.keys()
                    domain = src["domain"] if "domain" in keys and src["domain"] else ""
                    counts = _count_leads_for_source(
                        conn, aid, src["id"], src["name"], domain, src["base_url"]
                    )
                    conn.execute(
                        "UPDATE sources SET found_total = ?, found_today = ?, updated_at = ? WHERE id = ?",
                        (counts["total"], counts["touched_today"], _now(), src["id"]),
                    )
                conn.commit()
            return
        except Exception as exc:
            if "deadlock" in str(exc).lower() and attempt < 3:
                import time

                time.sleep(0.25 * (attempt + 1))
                continue
            logger.warning("recalc_source_found_counts: %s", str(exc)[:160])
            return


def delete_lead(lead_id: int, agency_id: str) -> bool:
    with get_connection() as conn:
        row = conn.execute(
            "SELECT id, first_name, last_name FROM leads WHERE id = ? AND agency_id = ?",
            (lead_id, agency_id),
        ).fetchone()
        if not row:
            return False
        owner = " ".join(p for p in (row["first_name"], row["last_name"]) if p) or f"#{lead_id}"
        conn.execute("DELETE FROM leads WHERE id = ? AND agency_id = ?", (lead_id, agency_id))
        conn.commit()
    try:
        from crm.leads.images import delete_lead_images

        delete_lead_images(agency_id, lead_id)
    except Exception:
        logger.exception("delete lead images %s", lead_id)
    recalc_source_found_counts(agency_id)
    invalidate_leads_snapshot(agency_id)
    add_activity("lead", f"Prospect supprimé — {owner}", agency_id)
    return True


def delete_all_leads(agency_id: str) -> int:
    with get_connection() as conn:
        count = conn.execute(
            "SELECT COUNT(*) AS c FROM leads WHERE agency_id = ?", (agency_id,)
        ).fetchone()["c"]
        conn.execute("DELETE FROM leads WHERE agency_id = ?", (agency_id,))
        conn.commit()
    recalc_source_found_counts(agency_id)
    invalidate_leads_snapshot(agency_id)
    if count:
        add_activity("lead", f"Tous les prospects supprimés ({count})", agency_id)
    return count


def _row_to_lead(row: sqlite3.Row, *, enrich_scores: bool = True) -> dict:
    from crm.scoring.recalc import enrich_lead_scores as enrich_lead_row
    from crawler.hub_detection import (
        detect_property_type,
        is_listing_title_name,
        parse_property_detail,
        parse_property_label,
    )
    from crawler.validation import _name_ok

    keys = row.keys()
    listing_type = row["listing_type"] if "listing_type" in keys else row["type"]
    try:
        missing = json.loads(row["missing_fields"] or "[]")
    except (json.JSONDecodeError, TypeError):
        missing = []
    surface = row["surface"]
    listing_title = row["listing_title"] if "listing_title" in keys else None
    address = row["address"] or "—"
    city = row["city"] if "city" in keys and row["city"] else None
    postcode = row["postcode"] if "postcode" in keys and row["postcode"] else None
    sector = row["sector"] if "sector" in keys and row["sector"] else None
    if not city or not sector:
        from crm.dvf import extract_listing_location

        loc = extract_listing_location(
            address if address != "—" else None,
            listing_title,
            city,
        )
        city = city or loc.get("city") or _extract_city(row["address"])
        postcode = postcode or loc.get("postcode")
        sector = sector or loc.get("sector")
    else:
        city = city or _extract_city(row["address"])

    from crawler.address_quality import sanitize_location_triplet

    addr_clean = address if address != "—" else None
    addr_clean, city, postcode = sanitize_location_triplet(addr_clean, city, postcode)
    if addr_clean:
        address = addr_clean

    raw_first = row["first_name"]
    raw_last = row["last_name"]
    agency_name = row["agency"] if "agency" in keys else None

    if _name_ok(raw_first, raw_last) and not is_listing_title_name(raw_first, raw_last):
        owner = " ".join(p for p in (raw_first, raw_last) if p)
    elif listing_type == "agence" and agency_name:
        owner = agency_name
    else:
        owner = "Particulier"

    property_title = parse_property_label(listing_title, address if address != "—" else None, surface=surface)
    if property_title == "—" and listing_title and not is_listing_title_name(listing_title):
        property_title = listing_title[:100]
    elif is_listing_title_name(raw_first, raw_last) and not listing_title:
        property_title = parse_property_label(
            " ".join(p for p in (raw_first, raw_last) if p),
            None,
            surface=surface,
        )

    property_detail = parse_property_detail(
        address if address != "—" else None,
        surface=surface,
        city=city,
    )

    # Type de bien toujours renseigné (Appartement, Maison…). Détecté depuis le
    # titre/adresse ; à défaut « Appartement » (cas le plus fréquent) pour ne
    # jamais laisser le champ vide dans l'UI / les exports.
    property_type = (
        detect_property_type(listing_title, property_title, address if address != "—" else None)
        or "Appartement"
    )

    base = {
        "id": row["id"],
        "owner": owner or "Particulier",
        "first_name": raw_first,
        "last_name": raw_last,
        "phone": row["phone"] or "—",
        "email": row["email"] or "—",
        "address": address,
        "surface": surface,
        "property": property_detail,
        "property_title": property_title,
        "property_detail": property_detail,
        "property_type": property_type,
        "price": row["price"] or 0,
        "previous_price": row["previous_price"] if "previous_price" in keys else None,
        "transaction_type": row["transaction_type"] if "transaction_type" in keys else "vente",
        "price_period": row["price_period"] if "price_period" in keys else None,
        "source": row["source"],
        "source_id": row["source_id"] if "source_id" in keys else None,
        "source_url": row["source_url"],
        "status": row["status"],
        "pipeline": row["pipeline"] or "nouveau",
        "type": listing_type,
        "agency": row["agency"],
        "score": row["score"],
        "mandate_score": row["mandate_score"] if "mandate_score" in keys else 0,
        "mandate_score_reason": (
            row["mandate_score_reason"] if "mandate_score_reason" in keys else ""
        ),
        "notes": row["notes"] if "notes" in keys else "",
        "next_follow_up": (
            _iso_datetime_str(row["next_follow_up"])
            if "next_follow_up" in keys and row["next_follow_up"]
            else None
        ),
        "missing_fields": missing,
        "published_at": (
            _iso_date_prefix(row["published_at"])
            if "published_at" in keys and row["published_at"]
            else None
        ),
        "created_at": (
            _iso_datetime_str(row["created_at"]) if "created_at" in keys else None
        ),
        "updated_at": (
            _iso_datetime_str(row["updated_at"]) if "updated_at" in keys else None
        ),
        "listedAt": (
            _iso_date_prefix(row["published_at"])
            if "published_at" in keys and row["published_at"]
            else ""
        ),
        "detected_at": (
            _iso_date_prefix(row["created_at"]) if "created_at" in keys else ""
        ),
        "city": city,
        "postcode": postcode,
        "sector": sector,
        "description": "",
        "dvf_median_m2": row["dvf_median_m2"] if "dvf_median_m2" in keys else None,
        "dvf_delta_pct": row["dvf_delta_pct"] if "dvf_delta_pct" in keys else None,
        "dvf_verdict": row["dvf_verdict"] if "dvf_verdict" in keys else None,
        "dvf_verdict_label": row["dvf_verdict_label"] if "dvf_verdict_label" in keys else None,
        "dvf_commune": row["dvf_commune"] if "dvf_commune" in keys else None,
        "dvf_sector": row["dvf_sector"] if "dvf_sector" in keys else None,
        "dvf_reference_period": row["dvf_reference_period"] if "dvf_reference_period" in keys else None,
        "dvf_sample_count": row["dvf_sample_count"] if "dvf_sample_count" in keys else None,
        "dvf_compared_at": (
            _iso_datetime_str(row["dvf_compared_at"])
            if "dvf_compared_at" in keys and row["dvf_compared_at"]
            else None
        ),
        "listing_title": row["listing_title"] if "listing_title" in keys else None,
        "facts_audit": _safe_json_field(row["facts_audit"] if "facts_audit" in keys else None),
        "agency_id": row["agency_id"] if "agency_id" in keys else None,
        "price_change_count": row["price_change_count"] if "price_change_count" in keys else 0,
        "last_price_change_at": (
            _iso_datetime_str(row["last_price_change_at"])
            if "last_price_change_at" in keys and row["last_price_change_at"]
            else None
        ),
        "priority_tier": row["priority_tier"] if "priority_tier" in keys else None,
        # price_estimate / price_estimate_at sont rattachés par get_lead / get_leads
        # depuis la table dédiée lead_estimates (pas de colonne sur leads).
        "price_estimate": None,
        "price_estimate_at": None,
        "property_fingerprint": _compute_property_fingerprint(postcode, surface, row["price"] or 0),
        "relisted_at": row["relisted_at"] if "relisted_at" in keys else None,
    }
    try:
        from crm.leads.images import lead_image_meta_from_row

        base.update(lead_image_meta_from_row(row))
    except Exception:
        base.update({"has_image": False, "image_custom": False, "image_url": None})
    if "score_explanation" in keys and row["score_explanation"]:
        try:
            base["score_explanation"] = json.loads(row["score_explanation"])
        except json.JSONDecodeError:
            base["score_explanation"] = None
    if base.get("mandate_score") is not None and base.get("score_explanation") is not None:
        base["_scores_enriched"] = True
    # Probabilité de signature toujours présente, même quand enrich_lead_row
    # court-circuite (lead déjà enrichi en base).
    expl = base.get("score_explanation")
    if isinstance(expl, dict) and expl.get("signature_probability") is not None:
        base["signature_probability"] = expl.get("signature_probability")
        base["signature_band"] = expl.get("signature_band")
        base["signature_tone"] = expl.get("signature_tone")
        base["signature_label"] = expl.get("signature_label")
    else:
        from crm.scoring.probability import signature_probability

        sig = signature_probability(base, base.get("mandate_score") or 0)
        base["signature_probability"] = sig["probability"]
        base["signature_band"] = sig["band"]
        base["signature_tone"] = sig["tone"]
        base["signature_label"] = sig["label"]
    if enrich_scores:
        return enrich_lead_row(base)
    return base


def _extract_city(address: str | None) -> str:
    if not address:
        return ""
    from crawler.address_quality import looks_like_street_in_commune_field

    m = re.search(r"F-\d{5},\s*([^(]+)", address, re.I)
    if m:
        candidate = m.group(1).strip()
        if not looks_like_street_in_commune_field(candidate):
            return candidate
    m2 = re.search(r"[àa]\s+([A-Za-zÀ-ÿ\s'-]+?)\s*\(\d{2,3}\)", address)
    if m2:
        return m2.group(1).strip()
    parts = address.split(",")
    if len(parts) >= 2:
        city = parts[-1].strip()
        if looks_like_street_in_commune_field(city):
            return ""
    else:
        city = parts[-1].strip() if parts else ""
    m3 = re.match(r"^(.+?)\s*\(\d{2,3}\)\s*$", city)
    result = m3.group(1).strip() if m3 else city
    return "" if looks_like_street_in_commune_field(result) else result


def _source_url_like(domain: str, base_url: str) -> str:
    from urllib.parse import urlparse

    host = (domain or urlparse(base_url or "").netloc or "").lower().replace("www.", "")
    if not host:
        return "%"
    return f"%{host}%"


def _count_leads_for_source(
    conn: sqlite3.Connection,
    agency_id: str,
    source_id: str,
    name: str,
    domain: str,
    base_url: str,
) -> dict[str, int]:
    """Compte réel des prospects liés à une source (source_id, nom ou URL)."""
    today = _now()[:10]
    url_like = _source_url_like(domain, base_url)
    row = conn.execute(
        """SELECT
               COUNT(*) AS total,
               SUM(CASE WHEN substr(created_at, 1, 10) = ? THEN 1 ELSE 0 END) AS created_today,
               SUM(CASE WHEN substr(COALESCE(updated_at, created_at), 1, 10) = ? THEN 1 ELSE 0 END) AS touched_today
           FROM leads
           WHERE agency_id = ?
           AND (
               source_id = ?
               OR ((source_id IS NULL OR source_id = '') AND LOWER(source) = LOWER(?))
               OR source_url LIKE ?
           )""",
        (today, today, agency_id, source_id, name, url_like),
    ).fetchone()
    return {
        "total": int(row["total"] or 0),
        "created_today": int(row["created_today"] or 0),
        "touched_today": int(row["touched_today"] or 0),
    }


def sync_lead_source_ids(agency_id: str) -> int:
    """Rattache source_id depuis l'URL quand il manque (ex. anciens crawls Bien'ici)."""
    if not agency_id:
        return 0
    fixed = 0
    with get_connection() as conn:
        sources = conn.execute(
            "SELECT id, domain, base_url FROM sources WHERE agency_id = ?",
            (agency_id,),
        ).fetchall()
        for src in sources:
            pattern = _source_url_like(src["domain"] if "domain" in src.keys() else "", src["base_url"])
            if pattern == "%":
                continue
            cur = conn.execute(
                """UPDATE leads SET source_id = ?, updated_at = ?
                   WHERE agency_id = ?
                   AND (source_id IS NULL OR source_id = '')
                   AND source_url LIKE ?""",
                (src["id"], _now(), agency_id, pattern),
            )
            fixed += cur.rowcount or 0
        if fixed:
            conn.commit()
    return fixed


def _batch_lead_counts_by_source_id(conn, agency_id: str) -> dict[str, dict[str, int]]:
    today_start = date.today().isoformat()
    tomorrow_start = (date.today() + timedelta(days=1)).isoformat()
    rows = conn.execute(
        """SELECT source_id,
                  COUNT(*) AS total,
                  SUM(CASE WHEN created_at >= ? AND created_at < ? THEN 1 ELSE 0 END) AS created_today,
                  SUM(CASE WHEN COALESCE(updated_at, created_at) >= ?
                            AND COALESCE(updated_at, created_at) < ? THEN 1 ELSE 0 END) AS touched_today
           FROM leads
           WHERE agency_id = ?
             AND source_id IS NOT NULL AND source_id != ''
           GROUP BY source_id""",
        (today_start, tomorrow_start, today_start, tomorrow_start, agency_id),
    ).fetchall()
    out: dict[str, dict[str, int]] = {}
    for r in rows:
        sid = r["source_id"]
        if not sid:
            continue
        out[str(sid)] = {
            "total": int(r["total"] or 0),
            "created_today": int(r["created_today"] or 0),
            "touched_today": int(r["touched_today"] or 0),
        }
    return out


def get_sources(
    agency_id: str,
    *,
    sync: bool = False,
    live_counts: bool | None = None,
) -> list[dict]:
    from crm.leads.shared_pool import is_shared_pool_agency_id

    if is_shared_pool_agency_id(agency_id) or str(agency_id or "").strip().lower() == "none":
        return []
    if live_counts is None:
        live_counts = sync
    if not sync:
        cached = _sources_cache.get(agency_id)
        if cached and (time.monotonic() - cached[0]) < _SOURCES_CACHE_TTL_SEC:
            return list(cached[1])
    if sync:
        sync_default_sources_for_agency(agency_id)
        from crawler.config import CRAWL_INCLUDE_CATALOG_IN_AUTO
        from crawler.immobilier_catalog import sync_immobilier_catalog_for_agency

        if CRAWL_INCLUDE_CATALOG_IN_AUTO:
            sync_immobilier_catalog_for_agency(agency_id)
        sync_lead_source_ids(agency_id)
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT * FROM sources WHERE agency_id = ? ORDER BY name",
            (agency_id,),
        ).fetchall()
        batch: dict[str, dict[str, int]] = {}
        if live_counts:
            batch = _batch_lead_counts_by_source_id(conn, agency_id)
        out = [
            _row_to_source(
                r,
                conn,
                agency_id,
                live_counts=live_counts,
                counts_by_source_id=batch,
            )
            for r in rows
        ]
    result = sorted(out, key=_source_sort_key)
    _sources_cache[agency_id] = (time.monotonic(), result)
    return result


def get_source(source_id: str, agency_id: str) -> dict | None:
    sync_lead_source_ids(agency_id)
    with get_connection() as conn:
        row = conn.execute(
            "SELECT * FROM sources WHERE id = ? AND agency_id = ?",
            (source_id, agency_id),
        ).fetchone()
        return _row_to_source(row, conn, agency_id) if row else None


def _row_to_source(
    r: sqlite3.Row,
    conn: sqlite3.Connection | None = None,
    agency_id: str | None = None,
    *,
    live_counts: bool = True,
    counts_by_source_id: dict[str, dict[str, int]] | None = None,
):
    from crawler.immobilier_catalog import resolve_catalog_id
    from crawler.url_utils import logo_fallback_for_domain, logo_url_for_domain, registrable_domain
    from urllib.parse import urlparse

    keys = r.keys()
    is_custom = bool(r["is_custom"]) if "is_custom" in keys else str(r["id"]).startswith("custom-")
    domain = r["domain"] if "domain" in keys and r["domain"] else registrable_domain(
        urlparse(r["base_url"]).netloc
    )
    from crawler.portals import resolve_base_portal_id

    portal_id = resolve_base_portal_id(r["id"])
    if portal_id == "streamestate":
        domain = "veliora.fr"
        logo_url = None
        logo_fallback = None
    else:
        logo_url = r["logo_url"] if "logo_url" in keys and r["logo_url"] else logo_url_for_domain(domain)
        logo_fallback = (
            r["logo_fallback"]
            if "logo_fallback" in keys and r["logo_fallback"]
            else logo_fallback_for_domain(domain)
        )
    leads_count = int(r["found_total"] or 0)
    leads_updated_today = int(r["found_today"] or 0)
    leads_created_today = 0
    if live_counts and agency_id:
        sid = str(r["id"])
        batch = (counts_by_source_id or {}).get(sid)
        if batch is not None:
            leads_count = batch["total"]
            leads_updated_today = batch["touched_today"]
            leads_created_today = batch["created_today"]
        elif conn is not None:
            counts = _count_leads_for_source(
                conn, agency_id, r["id"], r["name"], domain, r["base_url"]
            )
            leads_count = counts["total"]
            leads_updated_today = counts["touched_today"]
            leads_created_today = counts["created_today"]

    return {
        "id": r["id"],
        "name": r["name"],
        "base_url": r["base_url"],
        "search_url": r["search_url"],
        "domain": domain,
        "enabled": bool(r["enabled"]),
        "found": leads_count,
        "today": leads_updated_today,
        "leads_count": leads_count,
        "leads_updated_today": leads_updated_today,
        "leads_created_today": leads_created_today,
        "progress": 100 if leads_count > 0 else 0,
        "last_scan": r["last_scan"],
        "last_error": r["last_error"],
        "is_custom": is_custom,
        "is_catalog": bool(resolve_catalog_id(r["id"])),
        "is_default_portal": is_default_portal_source(r["id"]),
        "is_antibot": is_antibot_source(
            {"id": r["id"], "base_url": r["base_url"], "search_url": r["search_url"]}
        ),
        "is_protected_portal": is_protected_portal_source(
            {"id": r["id"], "base_url": r["base_url"], "search_url": r["search_url"]}
        ),
        "logo_url": logo_url,
        "logo_fallback": logo_fallback,
    }


def add_source(
    agency_id: str,
    name: str | None = None,
    base_url: str | None = None,
    search_url: str | None = None,
    url: str | None = None,
) -> dict:
    """Ajoute ou met à jour une source. Passez `url` seul pour un lien simple."""
    from crawler.url_utils import parse_site_url

    if url:
        parsed = parse_site_url(url)
        name = (name or parsed["name"]).strip()
        base_url = parsed["base_url"]
        search_url = parsed["search_url"]
        base_source_id = parsed["id"]
        is_custom = parsed["is_custom"]
        domain = parsed["domain"]
        logo_url = parsed["logo_url"]
        logo_fallback = parsed["logo_fallback"]
    else:
        if not base_url:
            raise ValueError("Lien requis")
        parsed = parse_site_url(search_url or base_url)
        name = (name or parsed["name"]).strip()
        base_source_id = parsed["id"]
        is_custom = parsed["is_custom"]
        domain = parsed["domain"]
        logo_url = parsed["logo_url"]
        logo_fallback = parsed["logo_fallback"]
        search_url = (search_url or base_url).strip().rstrip("/")
        if not search_url.startswith(("http://", "https://")):
            search_url = "https://" + search_url
        from urllib.parse import urlparse
        p = urlparse(base_url if base_url.startswith("http") else "https://" + base_url)
        base_url = f"{p.scheme}://{p.netloc}"

    if not name:
        raise ValueError("Nom ou lien requis")

    from crm.config import MAX_SOURCES_PER_AGENCY

    existing_count = len(get_sources(agency_id))
    source_id = scoped_source_id(agency_id, base_source_id)
    already = get_source(source_id, agency_id)
    if not already and MAX_SOURCES_PER_AGENCY > 0 and existing_count >= MAX_SOURCES_PER_AGENCY:
        raise ValueError(
            f"Limite de {MAX_SOURCES_PER_AGENCY} sources atteinte pour votre agence. "
            "Supprimez une source ou contactez le support."
        )

    now = _now()
    with get_connection() as conn:
        conn.execute(
            """INSERT INTO sources
               (id, name, base_url, search_url, enabled, is_custom,
                domain, logo_url, logo_fallback, agency_id, created_at, updated_at)
               VALUES (?, ?, ?, ?, 1, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(id) DO UPDATE SET
                 name = excluded.name,
                 base_url = excluded.base_url,
                 search_url = excluded.search_url,
                 is_custom = excluded.is_custom,
                 domain = excluded.domain,
                 logo_url = excluded.logo_url,
                 logo_fallback = excluded.logo_fallback,
                 agency_id = excluded.agency_id,
                 updated_at = excluded.updated_at""",
            (
                source_id,
                name.strip(),
                base_url.rstrip("/"),
                search_url.rstrip("/"),
                1 if is_custom else 0,
                domain,
                logo_url,
                logo_fallback,
                agency_id,
                now,
                now,
            ),
        )
        conn.commit()
        row = conn.execute(
            "SELECT * FROM sources WHERE id = ? AND agency_id = ?",
            (source_id, agency_id),
        ).fetchone()
        out = _row_to_source(row, conn, agency_id) if row else {}
    invalidate_sources_cache(agency_id)
    add_activity("crawl", f"Source configurée — {name} ({search_url})", agency_id)
    return out


def update_source_fields(
    source_id: str,
    agency_id: str,
    *,
    enabled: bool | None = None,
    url: str | None = None,
    name: str | None = None,
) -> dict | None:
    """Met à jour une source (toggle, lien de crawl, nom). Conserve l’id en base."""
    from crawler.url_utils import parse_site_url

    with get_connection() as conn:
        row = conn.execute(
            "SELECT * FROM sources WHERE id = ? AND agency_id = ?",
            (source_id, agency_id),
        ).fetchone()
        if not row:
            return None

        now = _now()
        sets = ["updated_at = ?"]
        values: list = [now]

        if enabled is not None:
            sets.append("enabled = ?")
            values.append(1 if enabled else 0)

        if url:
            parsed = parse_site_url(url)
            display_name = (name or parsed["name"] or row["name"]).strip()
            sets.extend([
                "name = ?",
                "base_url = ?",
                "search_url = ?",
                "domain = ?",
                "logo_url = ?",
                "logo_fallback = ?",
                "last_error = NULL",
            ])
            values.extend([
                display_name,
                parsed["base_url"],
                parsed["search_url"],
                parsed["domain"],
                parsed["logo_url"],
                parsed["logo_fallback"],
            ])

        if not url and name:
            sets.append("name = ?")
            values.append(name.strip())

        values.append(source_id)
        conn.execute(
            f"UPDATE sources SET {', '.join(sets)} WHERE id = ?",
            values,
        )
        conn.commit()
        updated = conn.execute(
            "SELECT * FROM sources WHERE id = ? AND agency_id = ?",
            (source_id, agency_id),
        ).fetchone()
        out = _row_to_source(updated, conn, agency_id) if updated else {}

    if url or name:
        add_activity("crawl", f"Source mise à jour — {updated['name']}", agency_id)
    invalidate_sources_cache(agency_id)
    return out


def delete_source(source_id: str, agency_id: str) -> bool:
    if is_default_portal_source(source_id):
        raise ValueError(
            "Les portails Veliora (LeBonCoin, PAP, SeLoger…) ne peuvent pas être supprimés — "
            "désactivez-les avec l’interrupteur si besoin."
        )
    with get_connection() as conn:
        row = conn.execute(
            "SELECT id FROM sources WHERE id = ? AND agency_id = ?",
            (source_id, agency_id),
        ).fetchone()
        if not row:
            return False
        conn.execute(
            "DELETE FROM sources WHERE id = ? AND agency_id = ?",
            (source_id, agency_id),
        )
        conn.commit()
    invalidate_sources_cache(agency_id)
    add_activity("crawl", f"Source supprimée — {source_id}", agency_id)
    return True


def refresh_source_names_and_logos() -> None:
    """Recalcule noms et logos pour les sources custom (corrige anciens noms erronés)."""
    from crawler.portals import resolve_base_portal_id
    from crawler.url_utils import parse_site_url

    with get_connection() as conn:
        rows = conn.execute("SELECT * FROM sources").fetchall()
        for row in rows:
            if resolve_base_portal_id(row["id"]) and not row["is_custom"]:
                continue
            try:
                parsed = parse_site_url(row["search_url"] or row["base_url"])
            except ValueError:
                continue
            bad_names = {"immobilier", "immo", "vente", "achat", "location", "annonces", "recherche"}
            name = (row["name"] or "").strip()
            if name.lower() not in bad_names and resolve_base_portal_id(row["id"]):
                continue
            conn.execute(
                """UPDATE sources SET name = ?, domain = ?, logo_url = ?, logo_fallback = ?,
                   updated_at = ? WHERE id = ?""",
                (
                    parsed["name"],
                    parsed["domain"],
                    parsed["logo_url"],
                    parsed["logo_fallback"],
                    _now(),
                    row["id"],
                ),
            )
        conn.commit()


def mark_source_scanned(source_id: str, error: str | None = None) -> None:
    with get_connection() as conn:
        conn.execute(
            "UPDATE sources SET last_scan = ?, last_error = ?, updated_at = ? WHERE id = ?",
            (_now(), error, _now(), source_id),
        )
        conn.commit()


def get_activities(agency_id: str, limit: int = 20) -> list[dict]:
    with get_connection() as conn:
        rows = conn.execute(
            """SELECT * FROM activities WHERE agency_id = ?
               ORDER BY created_at DESC LIMIT ?""",
            (agency_id, limit),
        ).fetchall()
        return [
            {"type": r["type"], "text": r["text"], "time": _relative_time(r["created_at"])}
            for r in rows
        ]


def compute_stats_from_leads(leads: list[dict]) -> dict:
    """Compteurs dashboard — sans requête DB si la liste est déjà chargée."""
    total = len(leads)
    sans_agence = sum(
        1 for l in leads if (l.get("listing_type") or l.get("type") or "particulier") != "agence"
    )
    nouveaux = sum(1 for l in leads if (l.get("status") or "") == "nouveau")
    mandats = sum(1 for l in leads if (l.get("status") or "") == "mandat")
    particuliers = sum(
        1 for l in leads if (l.get("listing_type") or l.get("type") or "particulier") == "particulier"
    )
    return {
        "total": total,
        "sans_agence": sans_agence,
        "nouveaux": nouveaux,
        "mandats": mandats,
        "particuliers": particuliers,
    }


def _row_to_stats_lead(row) -> dict:
    keys = row.keys()
    return {
        "agency_id": row["agency_id"] if "agency_id" in keys else None,
        "listing_type": row["listing_type"] if "listing_type" in keys else None,
        "type": row["type"] if "type" in keys else None,
        "status": row["status"] if "status" in keys else None,
        "city": row["city"] if "city" in keys else None,
        "postcode": row["postcode"] if "postcode" in keys else None,
        "sector": row["sector"] if "sector" in keys else None,
        "address": row["address"] if "address" in keys else None,
    }


def _fetch_leads_for_stats(agency_id: str) -> list[dict]:
    """Compteurs sans parsing complet ni jointures transactions."""
    from crm.leads.shared_pool import filter_leads_for_agency, shared_leads_sql_where

    pool_where = shared_leads_sql_where()
    with get_connection() as conn:
        rows = conn.execute(
            f"""SELECT {_STATS_LEADS_SQL} FROM leads
               WHERE {pool_where} OR agency_id = ?""",
            (agency_id,),
        ).fetchall()
    leads = [_row_to_stats_lead(r) for r in rows]
    return filter_leads_for_agency(leads, agency_id)


def get_stats(agency_id: str, *, leads: list[dict] | None = None) -> dict:
    if leads is not None:
        return compute_stats_from_leads(leads)
    return compute_stats_from_leads(_fetch_leads_for_stats(agency_id))


def get_source_stats(agency_id: str) -> list[dict]:
    with get_connection() as conn:
        return _source_stats_from_conn(conn, agency_id)


def _source_stats_from_conn(conn, agency_id: str) -> list[dict]:
    rows = conn.execute(
        """SELECT name, found_total FROM sources
           WHERE agency_id = ? ORDER BY found_total DESC""",
        (agency_id,),
    ).fetchall()
    total = sum(r["found_total"] for r in rows) or 1
    return [
        {
            "name": r["name"] or "Source",
            "key": (r["name"] or "source").lower().replace("'", "").replace(" ", ""),
            "count": r["found_total"],
            "pct": round((r["found_total"] / total) * 100) if total else 0,
        }
        for r in rows
    ]


def get_bootstrap_sidebar(agency_id: str, *, activity_limit: int = 20) -> dict:
    """Activités + stats sources en une seule connexion (bootstrap CRM)."""
    with get_connection() as conn:
        rows = conn.execute(
            """SELECT * FROM activities WHERE agency_id = ?
               ORDER BY created_at DESC LIMIT ?""",
            (agency_id, activity_limit),
        ).fetchall()
        activities = [
            {"type": r["type"], "text": r["text"], "time": _relative_time(r["created_at"])}
            for r in rows
        ]
        return {
            "activities": activities,
            "source_stats": _source_stats_from_conn(conn, agency_id),
        }


def schedule_bootstrap_housekeeping(agency_id: str, *, delay_sec: float = 12.0) -> None:
    """Rattache orphelins + sync catalogues sources — hors chemin critique bootstrap."""

    def _worker() -> None:
        try:
            if delay_sec > 0:
                time.sleep(delay_sec)
            n = claim_orphan_leads(agency_id)
            if n:
                invalidate_leads_snapshot(agency_id)
            get_sources(agency_id, sync=True, live_counts=True)
        except Exception:
            logger.exception("bootstrap housekeeping agency=%s", agency_id)

    threading.Thread(
        target=_worker,
        name=f"veliora-bootstrap-{str(agency_id)[:8]}",
        daemon=True,
    ).start()


def _relative_time(iso: str) -> str:
    try:
        dt = datetime.fromisoformat(iso)
        delta = datetime.now(timezone.utc) - dt.replace(tzinfo=timezone.utc)
        mins = int(delta.total_seconds() / 60)
        if mins < 1:
            return "À l'instant"
        if mins < 60:
            return f"Il y a {mins} min"
        hours = mins // 60
        if hours < 24:
            return f"Il y a {hours}h"
        return "Hier"
    except (ValueError, TypeError):
        return ""


_PIPELINE_STATUS = {
    "nouveau": "nouveau",
    "a_contacter": "nouveau",
    "contacte": "contacte",
    "rdv": "contacte",
    "mandat": "mandat",
    "perdu": "perdu",
}


def _normalize_patch_lead_field(key: str, value: object) -> object | None:
    """Normalise une valeur saisie CRM avant UPDATE."""
    from crawler.extractors import normalize_phone

    if value is None:
        return None
    if isinstance(value, str):
        value = value.strip()
        if value in ("", "—"):
            return None
    col = "listing_type" if key == "type" else key
    if col == "phone":
        if not value:
            return None
        return normalize_phone(str(value)) or None
    if col == "email":
        return str(value).lower() if value else None
    if col == "surface":
        if value is None or value == "":
            return None
        try:
            return float(str(value).replace(",", "."))
        except (TypeError, ValueError):
            return None
    if col == "price":
        if value is None or value == "":
            return None
        try:
            return int(float(str(value).replace(" ", "").replace(",", ".")))
        except (TypeError, ValueError):
            return None
    if col in ("first_name", "last_name", "address", "city", "postcode", "sector", "agency", "source_url", "notes"):
        return str(value)[:500] if value else None
    if col == "listing_type" and value in ("particulier", "agence"):
        return value
    if col == "transaction_type" and value in ("vente", "location"):
        return value
    if col == "next_follow_up":
        if not value:
            return None
        s = str(value).strip()
        if not s:
            return None
        if len(s) == 10 and s[4] == "-" and s[7] == "-":
            return f"{s}T12:00:00Z"
        return _iso_datetime_str(s) or _coerce_timestamp(s)
    return value


def patch_lead(lead_id: int, agency_id: str, data: dict) -> dict | None:
    """Met à jour pipeline, notes, champs contact/bien (saisie CRM)."""
    from crm.constants import LEAD_PATCH_FIELDS

    allowed = set(LEAD_PATCH_FIELDS)
    updates: dict[str, object] = {}
    for key in allowed:
        if key not in data:
            continue
        col = "listing_type" if key == "type" else key
        if col in updates:
            continue
        norm = _normalize_patch_lead_field(key, data[key])
        if norm is not None or data[key] in ("", "—", None):
            updates[col] = norm

    if "pipeline" in updates and "status" not in updates:
        updates["status"] = _PIPELINE_STATUS.get(str(updates["pipeline"]), "nouveau")

    if not updates:
        return get_lead(lead_id, agency_id)

    updates["updated_at"] = _now()
    set_clause = ", ".join(f"{k} = ?" for k in updates)
    values = list(updates.values()) + [lead_id, agency_id]

    lead_before = get_lead(lead_id, agency_id)
    old_pipeline = (lead_before or {}).get("pipeline")

    with get_connection() as conn:
        row = conn.execute(
            "SELECT id FROM leads WHERE id = ? AND agency_id = ?",
            (lead_id, agency_id),
        ).fetchone()
        if not row:
            return None

        conn.execute(
            f"UPDATE leads SET {set_clause} WHERE id = ? AND agency_id = ?",
            values,
        )
        if "pipeline" in updates:
            label = updates["pipeline"]
            conn.execute(
                "INSERT INTO activities (type, text, agency_id, created_at) VALUES (?, ?, ?, ?)",
                ("contact", f"Pipeline → {label}", agency_id, _now()),
            )
            from crm.scoring.outcomes import (
                calibrate_agency_weights_from_outcome,
                pipeline_to_outcome,
                record_lead_outcome,
            )
            from crm.scoring.recalc import scores_snapshot_from_lead

            new_pl = str(updates["pipeline"])
            if old_pipeline != new_pl:
                outcome = pipeline_to_outcome(new_pl)
                if outcome and lead_before:
                    snap = scores_snapshot_from_lead(lead_before)
                    record_lead_outcome(
                        conn,
                        lead_id=lead_id,
                        agency_id=agency_id,
                        outcome_type=outcome,
                        scores_snapshot=snap,
                    )
                    calibrate_agency_weights_from_outcome(
                        conn, agency_id, outcome, snap
                    )
        conn.commit()

    if any(
        k in updates
        for k in (
            "first_name",
            "last_name",
            "phone",
            "email",
            "address",
            "surface",
            "price",
        )
    ):
        try:
            from crawler.validation import lead_from_db_row, sanitize_lead

            row = get_lead(lead_id, agency_id)
            if row:
                ld = sanitize_lead(lead_from_db_row(row))
                missing_json = json.dumps(ld.missing_fields())
                with get_connection() as conn:
                    conn.execute(
                        "UPDATE leads SET missing_fields = ?, updated_at = ? WHERE id = ? AND agency_id = ?",
                        (missing_json, _now(), lead_id, agency_id),
                    )
                    conn.commit()
        except Exception as exc:
            logger.warning("patch_lead missing_fields lead %s: %s", lead_id, exc)

    lead = get_lead(lead_id, agency_id)
    if lead:
        try:
            from crm.scoring.recalc import enrich_lead_scores

            enriched = enrich_lead_scores(dict(lead))
            persist_lead_scores(lead_id, agency_id, enriched)
            lead = get_lead(lead_id, agency_id)
        except Exception as exc:
            logger.warning("patch_lead scoring lead %s: %s", lead_id, exc)
    if lead and updates.get("pipeline") == "mandat":
        add_activity("mandat", f"Mandat signé — {lead.get('address', '')}", agency_id)
    if lead and any(
        k in updates
        for k in ("first_name", "last_name", "phone", "email", "address", "price", "surface")
    ):
        add_activity(
            "lead",
            f"Fiche mise à jour — {lead.get('address', '') or lead.get('owner', '')}",
            agency_id,
        )
    invalidate_leads_snapshot(agency_id)
    return lead


def get_agency_id_by_slug(slug: str) -> str | None:
    """ID agence depuis le slug public (vitrine estimateur, liens personnalisés)."""
    slug = (slug or "").strip().lower()
    if not slug:
        return None
    with get_connection() as conn:
        row = conn.execute(
            "SELECT id FROM agencies WHERE lower(slug) = ?",
            (slug,),
        ).fetchone()
    return str(row["id"]) if row else None


def get_agency_name(agency_id: str) -> str:
    with get_connection() as conn:
        row = conn.execute(
            "SELECT name FROM agencies WHERE id = ?", (agency_id,)
        ).fetchone()
    return row["name"] if row else ""


def list_agency_ids() -> list[str]:
    with get_connection() as conn:
        rows = conn.execute("SELECT id FROM agencies ORDER BY created_at").fetchall()
    return [r["id"] for r in rows]


def _agency_settings_from_row(row) -> dict:
    if not row:
        return {
            "target_cities": [],
            "target_neighborhoods": [],
            "mandate_goal_month": 5,
            "onboarding_step": 0,
            "onboarding_completed": False,
            "primary_city": None,
        }
    keys = row.keys()
    try:
        target_cities = json.loads(row["target_cities"] or "[]")
    except (json.JSONDecodeError, TypeError):
        target_cities = []
    try:
        target_neighborhoods = json.loads(row["target_neighborhoods"] or "[]")
    except (json.JSONDecodeError, TypeError):
        target_neighborhoods = []
    settings = {
        "target_cities": target_cities,
        "target_neighborhoods": target_neighborhoods,
        "mandate_goal_month": row["mandate_goal_month"] or 5,
        "onboarding_step": row["onboarding_step"] if "onboarding_step" in keys else 0,
        "onboarding_completed": bool(row["onboarding_completed"])
        if "onboarding_completed" in keys
        else False,
    }
    settings["primary_city"] = _first_target_city(settings.get("target_cities"))
    return settings


def get_agency_settings(agency_id: str) -> dict:
    now = time.monotonic()
    hit = _settings_cache.get(agency_id)
    if hit and now - hit[0] < _SETTINGS_CACHE_TTL_SEC:
        return dict(hit[1])
    with get_connection() as conn:
        row = conn.execute(
            "SELECT * FROM agency_settings WHERE agency_id = ?", (agency_id,)
        ).fetchone()
    settings = _agency_settings_from_row(row)
    _settings_cache[agency_id] = (time.monotonic(), settings)
    return dict(settings)


def _first_target_city(cities: list | None) -> str | None:
    for c in cities or []:
        if c and str(c).strip():
            return str(c).strip()
    return None


def get_agency_primary_city(agency_id: str) -> str | None:
    """Ville principale de l'agence (1ʳᵉ ville cible) — filtre par défaut des crawls."""
    return _first_target_city(get_agency_settings(agency_id).get("target_cities"))


def resolve_crawl_city(
    city: str | None = None,
    *,
    agency_id: str | None = None,
    request_data: dict | None = None,
) -> str | None:
    """
    Ville effective du crawl — même règles que POST /api/crawler/scan.
    Corps avec clé city/ville : valeur explicite ('' = national).
    Sinon : 1ʳᵉ ville du territoire agence, ou national (None) si territoire vide.
    """
    if request_data is not None and ("city" in request_data or "ville" in request_data):
        raw = (request_data.get("city") or request_data.get("ville") or "").strip()
        return raw or None
    if city is not None:
        normalized = (city or "").strip()
        return normalized or None
    if agency_id:
        return get_agency_primary_city(agency_id)
    return None


def get_agency_postcode_for_city(agency_id: str | None, city: str | None) -> str | None:
    """Code postal agence si la ville cible correspond à la fiche agence."""
    if not agency_id or not city:
        return None
    from crm.mandates.storage import get_agency_legal_profile

    profile = get_agency_legal_profile(agency_id)
    pc = (profile.get("postal_code") or "").strip()
    profile_city = (profile.get("city") or "").strip()
    target = (city or "").strip().split("(")[0].strip()
    if pc and profile_city and profile_city.lower() == target.lower():
        return pc
    return None


def _sync_legal_profile_city(agency_id: str, city: str) -> None:
    """Aligne la ville de la fiche agence sur le territoire (1ʳᵉ ville cible)."""
    city = (city or "").strip()
    if not city or len(city) < 2:
        return
    try:
        from crm.mandates.storage import get_agency_legal_profile, upsert_agency_legal_profile

        profile = get_agency_legal_profile(agency_id)
        if (profile.get("city") or "").strip().lower() == city.lower():
            return
        upsert_agency_legal_profile(agency_id, {"city": city})
    except Exception:
        pass


def set_onboarding(agency_id: str, *, step: int | None = None, completed: bool | None = None) -> dict:
    current = get_agency_settings(agency_id)
    new_step = step if step is not None else current["onboarding_step"]
    new_done = completed if completed is not None else current["onboarding_completed"]
    if new_done:
        new_step = max(new_step, 3)
    now = _now()
    with get_connection() as conn:
        conn.execute(
            """INSERT INTO agency_settings
               (agency_id, target_cities, target_neighborhoods, mandate_goal_month,
                onboarding_step, onboarding_completed, updated_at)
               VALUES (?, '[]', '[]', 5, ?, ?, ?)
               ON CONFLICT(agency_id) DO UPDATE SET
               onboarding_step = excluded.onboarding_step,
               onboarding_completed = excluded.onboarding_completed,
               updated_at = excluded.updated_at""",
            (agency_id, int(new_step), 1 if new_done else 0, now),
        )
        conn.commit()
    invalidate_agency_settings_cache(agency_id)
    return get_agency_settings(agency_id)


def export_leads_csv(agency_id: str) -> str:
    """Export CSV des prospects de l'agence."""
    import csv
    import io

    leads = get_leads(agency_id)
    buf = io.StringIO()
    writer = csv.writer(buf, delimiter=";")
    writer.writerow(
        [
            "id",
            "owner",
            "phone",
            "email",
            "address",
            "city",
            "price",
            "previous_price",
            "surface",
            "transaction_type",
            "source",
            "source_url",
            "type",
            "status",
            "pipeline",
            "mandate_score",
            "mandate_score_reason",
            "dvf_verdict",
            "dvf_delta_pct",
            "published_at",
            "created_at",
            "updated_at",
            "notes",
        ]
    )
    for L in leads:
        writer.writerow(
            [
                L.get("id"),
                L.get("owner"),
                L.get("phone"),
                L.get("email"),
                L.get("address"),
                L.get("city"),
                L.get("price"),
                L.get("previous_price"),
                L.get("surface"),
                L.get("transaction_type"),
                L.get("source"),
                L.get("source_url"),
                L.get("type"),
                L.get("status"),
                L.get("pipeline"),
                L.get("mandate_score"),
                L.get("mandate_score_reason"),
                L.get("dvf_verdict"),
                L.get("dvf_delta_pct"),
                L.get("published_at"),
                L.get("created_at"),
                L.get("updated_at"),
                L.get("notes"),
            ]
        )
    return buf.getvalue()


def _should_recompare_dvf(lead: dict, *, force_recompare: bool) -> bool:
    if force_recompare:
        return True
    compared_raw = lead.get("dvf_compared_at")
    if not compared_raw or not str(compared_raw).strip():
        return True
    from crawler.config import DVF_RECOMPARE_HOURS

    try:
        compared = datetime.fromisoformat(str(compared_raw).replace("Z", "+00:00"))
        if compared.tzinfo is None:
            compared = compared.replace(tzinfo=timezone.utc)
        age_h = (datetime.now(timezone.utc) - compared).total_seconds() / 3600
        return age_h >= DVF_RECOMPARE_HOURS
    except (ValueError, TypeError):
        return True


def compare_lead_dvf(lead_id: int, agency_id: str) -> dict:
    """Compare un lead aux ventes DVF (Etalab) et persiste le résultat."""
    return compare_and_enrich_lead_dvf(lead_id, agency_id, force_recompare=True)


def compare_and_enrich_lead_dvf(
    lead_id: int,
    agency_id: str,
    *,
    force_recompare: bool = False,
) -> dict:
    """DVF Etalab + recalcul du score mandat (pour crawl parallèle ou API)."""
    from crm.dvf import compare_listing_to_dvf
    from crm.scoring.recalc import enrich_lead_scores as enrich_lead_row

    lead = get_lead(lead_id, agency_id)
    if not lead:
        return {"available": False, "reason": "Prospect introuvable", "lead_id": lead_id}

    if (lead.get("transaction_type") or "vente") != "vente":
        return {
            "available": False,
            "reason": "Comparatif DVF réservé aux ventes",
            "lead_id": lead_id,
        }

    if not lead.get("price") or not lead.get("surface"):
        return {
            "available": False,
            "reason": "Prix ou surface manquant",
            "lead_id": lead_id,
        }

    if not _should_recompare_dvf(lead, force_recompare=force_recompare):
        return {
            "available": bool(lead.get("dvf_verdict")),
            "reason": "Comparatif DVF déjà à jour",
            "lead_id": lead_id,
            "skipped": True,
            "verdict": lead.get("dvf_verdict"),
        }

    try:
        comp = compare_listing_to_dvf(
            lead.get("price"),
            lead.get("surface"),
            lead.get("address") or "",
            lead.get("city") or "",
            sector=lead.get("sector"),
            postcode=lead.get("postcode"),
            published_at=lead.get("published_at"),
            transaction_type=lead.get("transaction_type") or "vente",
        )
    except Exception as exc:
        return {
            "available": False,
            "reason": str(exc)[:200],
            "lead_id": lead_id,
            "error": True,
        }

    if comp.get("available"):
        lead["dvf_median_m2"] = comp.get("dvf_median_m2")
        lead["dvf_delta_pct"] = comp.get("delta_pct")
        lead["dvf_verdict"] = comp.get("verdict")
        lead["dvf_verdict_label"] = comp.get("verdict_label")
        lead["dvf_commune"] = comp.get("commune")
        lead["dvf_sector"] = comp.get("sector")
        lead["dvf_reference_period"] = comp.get("dvf_reference_period")
        lead["dvf_sample_count"] = comp.get("dvf_sample_count")
        lead["dvf_compared_at"] = comp.get("compared_at")
        if comp.get("listing_city") and not lead.get("city"):
            lead["city"] = comp.get("listing_city")
        if comp.get("listing_sector") and not lead.get("sector"):
            lead["sector"] = comp.get("listing_sector")
        if comp.get("listing_postcode") and not lead.get("postcode"):
            lead["postcode"] = comp.get("listing_postcode")
        from crm.scoring.recalc import enrich_lead_scores

        lead["agency_id"] = agency_id
        lead["id"] = lead_id
        enriched = enrich_lead_scores(lead)
        expl_json = json.dumps(
            enriched.get("score_explanation") or {},
            ensure_ascii=False,
        )
        with get_connection() as conn:
            conn.execute(
                """UPDATE leads SET
                   dvf_median_m2 = ?, dvf_delta_pct = ?, dvf_verdict = ?,
                   dvf_verdict_label = ?, dvf_commune = ?, dvf_sector = ?,
                   dvf_reference_period = ?, dvf_sample_count = ?,
                   city = COALESCE(city, ?), postcode = COALESCE(postcode, ?),
                   sector = COALESCE(sector, ?),
                   dvf_compared_at = ?, score = ?, mandate_score = ?,
                   mandate_score_reason = ?, priority_tier = ?,
                   score_explanation = ?, scores_computed_at = ?,
                   updated_at = ?
                   WHERE id = ? AND agency_id = ?""",
                (
                    comp.get("dvf_median_m2"),
                    comp.get("delta_pct"),
                    comp.get("verdict"),
                    comp.get("verdict_label"),
                    comp.get("commune"),
                    comp.get("sector"),
                    comp.get("dvf_reference_period"),
                    comp.get("dvf_sample_count"),
                    comp.get("listing_city"),
                    comp.get("listing_postcode"),
                    comp.get("listing_sector"),
                    _coerce_timestamp(comp.get("compared_at")),
                    enriched["mandate_score"],
                    enriched["mandate_score"],
                    enriched["mandate_score_reason"],
                    enriched.get("priority_tier"),
                    expl_json,
                    _now(),
                    _now(),
                    lead_id,
                    agency_id,
                ),
            )
            conn.commit()
    else:
        with get_connection() as conn:
            conn.execute(
                """UPDATE leads SET dvf_compared_at = ?, updated_at = ?
                   WHERE id = ? AND agency_id = ?""",
                (_coerce_timestamp(comp.get("compared_at")), _now(), lead_id, agency_id),
            )
            conn.commit()

    comp["lead_id"] = lead_id
    return comp


def compare_leads_dvf_batch(agency_id: str, limit: int = 30) -> dict:
    """Compare les leads vente sans comparatif DVF récent."""
    with get_connection() as conn:
        rows = conn.execute(
            f"""SELECT id FROM leads
               WHERE agency_id = ?
               AND COALESCE(transaction_type, 'vente') = 'vente'
               AND price > 0 AND surface > 0
               AND {_sql_dvf_not_compared_clause()}
               ORDER BY created_at DESC LIMIT ?""",
            (agency_id, limit),
        ).fetchall()

    done, errors = 0, 0
    results: list[dict] = []
    for row in rows:
        try:
            comp = compare_and_enrich_lead_dvf(row["id"], agency_id, force_recompare=True)
            if comp.get("available"):
                done += 1
            results.append({"lead_id": row["id"], "ok": comp.get("available"), "verdict": comp.get("verdict")})
        except Exception:
            errors += 1
    return {"compared": done, "errors": errors, "results": results}


def upsert_agency_settings(agency_id: str, data: dict) -> dict:
    current = get_agency_settings(agency_id)
    cities = data.get("target_cities", current["target_cities"])
    if isinstance(cities, str):
        cities = [s.strip() for s in cities.split(",") if s.strip()]
    elif not isinstance(cities, list):
        cities = current["target_cities"]
    neighborhoods = data.get("target_neighborhoods", current["target_neighborhoods"])
    goal = data.get("mandate_goal_month", current["mandate_goal_month"])
    now = _now()
    with get_connection() as conn:
        conn.execute(
            """INSERT INTO agency_settings
               (agency_id, target_cities, target_neighborhoods, mandate_goal_month, updated_at)
               VALUES (?, ?, ?, ?, ?)
               ON CONFLICT(agency_id) DO UPDATE SET
               target_cities = excluded.target_cities,
               target_neighborhoods = excluded.target_neighborhoods,
               mandate_goal_month = excluded.mandate_goal_month,
               updated_at = excluded.updated_at""",
            (
                agency_id,
                json.dumps(cities if isinstance(cities, list) else []),
                json.dumps(neighborhoods if isinstance(neighborhoods, list) else []),
                int(goal) if goal else 5,
                now,
            ),
        )
        conn.commit()
    primary = _first_target_city(cities if isinstance(cities, list) else [])
    if primary and data.get("target_cities") is not None:
        _sync_legal_profile_city(agency_id, primary)
    invalidate_agency_settings_cache(agency_id)
    # Le territoire (villes cibles) filtre les leads visibles : on purge l'instantané
    # pour que le briefing radar et toute vue en cache reflètent la nouvelle ville
    # immédiatement (sinon d'anciennes annonces hors secteur restent affichées le
    # temps du TTL).
    if data.get("target_cities") is not None:
        invalidate_leads_snapshot(agency_id)
    return get_agency_settings(agency_id)
