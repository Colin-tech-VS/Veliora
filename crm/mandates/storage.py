"""Persistance mandats vendeurs et fiche agence."""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone

from crawler.storage import get_connection, get_lead, get_agency_name

from crm.mandates.templates import (
    default_agency_profile,
    get_template_meta,
    render_mandate_html,
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def ensure_mandate_tables(conn) -> None:
    from crm.mandates.dossiers import ensure_dossier_tables

    ensure_dossier_tables(conn)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS agency_legal_profiles (
            agency_id TEXT PRIMARY KEY,
            profile_json TEXT NOT NULL DEFAULT '{}',
            updated_at TEXT NOT NULL
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS seller_mandates (
            id TEXT PRIMARY KEY,
            agency_id TEXT NOT NULL,
            lead_id INTEGER,
            mandate_type TEXT NOT NULL,
            exclusivity TEXT NOT NULL DEFAULT 'exclusif',
            status TEXT NOT NULL DEFAULT 'draft',
            title TEXT NOT NULL,
            fields_json TEXT NOT NULL DEFAULT '{}',
            body_html TEXT,
            recipient_email TEXT,
            sent_at TEXT,
            signed_at TEXT,
            notes TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS property_clients (
            id TEXT PRIMARY KEY,
            agency_id TEXT NOT NULL,
            segment TEXT NOT NULL,
            first_name TEXT,
            last_name TEXT,
            phone TEXT,
            email TEXT,
            budget_min INTEGER,
            budget_max INTEGER,
            property_type TEXT,
            rooms_min INTEGER,
            surface_min REAL,
            cities_json TEXT NOT NULL DEFAULT '[]',
            status TEXT NOT NULL DEFAULT 'actif',
            notes TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
    """)
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_seller_mandates_agency "
        "ON seller_mandates(agency_id)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_property_clients_agency "
        "ON property_clients(agency_id, segment)"
    )


def get_agency_legal_profile(agency_id: str) -> dict:
    with get_connection() as conn:
        ensure_mandate_tables(conn)
        row = conn.execute(
            "SELECT profile_json FROM agency_legal_profiles WHERE agency_id = ?",
            (agency_id,),
        ).fetchone()
    base = default_agency_profile()
    if row:
        try:
            base.update(json.loads(row["profile_json"] or "{}"))
        except json.JSONDecodeError:
            pass
    if not base.get("legal_name"):
        base["legal_name"] = get_agency_name(agency_id) or ""
        base["brand_name"] = base["legal_name"]
    return base


def upsert_agency_legal_profile(agency_id: str, data: dict) -> dict:
    prev = get_agency_legal_profile(agency_id)
    current = dict(prev)
    current.update({k: v for k, v in (data or {}).items() if v is not None})
    now = _now()
    with get_connection() as conn:
        ensure_mandate_tables(conn)
        conn.execute(
            """INSERT INTO agency_legal_profiles (agency_id, profile_json, updated_at)
               VALUES (?, ?, ?)
               ON CONFLICT(agency_id) DO UPDATE SET
               profile_json = excluded.profile_json,
               updated_at = excluded.updated_at""",
            (agency_id, json.dumps(current, ensure_ascii=False), now),
        )
        conn.commit()
    city = (current.get("city") or "").strip()
    prev_city = (prev.get("city") or "").strip()
    # Ne réordonne le territoire crawl que si la ville de la fiche a vraiment changé
    # (sinon une sauvegarde fiche avec l’ancienne ville écrase le Territoire Radar).
    if city and "city" in (data or {}) and city.lower() != prev_city.lower():
        from crawler.storage import get_agency_settings, upsert_agency_settings

        ag = get_agency_settings(agency_id)
        cities = list(ag.get("target_cities") or [])
        rest = [
            c
            for c in cities
            if c.strip().lower() not in (city.lower(), prev_city.lower())
        ]
        upsert_agency_settings(agency_id, {"target_cities": [city] + rest})
    return get_agency_legal_profile(agency_id)


def _parse_address_parts(address: str | None) -> dict:
    """Extrait code postal et ville depuis une adresse française."""
    import re

    if not address or address == "—":
        return {"postal_code": "", "city": "", "street": ""}
    m = re.search(r"\b(\d{5})\b", address)
    postal = m.group(1) if m else ""
    city = ""
    street = address
    if postal:
        parts = re.split(rf"\b{postal}\b", address, maxsplit=1)
        if len(parts) == 2:
            street = parts[0].strip(" ,")
            city = parts[1].strip(" ,")
        else:
            city = _extract_city_from_address(address)
    else:
        city = _extract_city_from_address(address)
    return {"postal_code": postal, "city": city, "street": street or address}


def _extract_city_from_address(address: str) -> str:
    parts = [p.strip() for p in address.split(",") if p.strip()]
    return parts[-1] if parts else ""


def _long_listing_label(days: int | None) -> str:
    if days is None:
        return ""
    if days > 90:
        return ">90 jours"
    if days > 60:
        return ">60 jours"
    if days > 30:
        return ">30 jours"
    return "Non"


def fields_from_lead(lead: dict, mandate_type: str) -> dict:
    """Préremplit les champs mandat depuis un prospect."""
    if not lead:
        return {}
    owner_parts = (lead.get("owner") or "—").split()
    fn = lead.get("first_name") or (owner_parts[0] if owner_parts else "")
    ln = lead.get("last_name") or (
        " ".join(owner_parts[1:]) if len(owner_parts) > 1 else ""
    )
    addr = lead.get("address") or ""
    addr_parts = _parse_address_parts(addr)
    is_particulier = lead.get("type") != "agence"
    days = lead.get("days_on_market")
    prev_price = lead.get("previous_price")
    price = lead.get("price") or 0
    drop_pct = lead.get("price_change_pct")
    if drop_pct is None and prev_price and price and prev_price > price:
        drop_pct = round((prev_price - price) / prev_price * 100, 1)

    common = {
        "property_address": addr_parts["street"] or addr,
        "postal_code": addr_parts["postal_code"],
        "city": lead.get("city") or addr_parts["city"],
        "seller_first_name": fn,
        "seller_last_name": ln,
        "seller_email": lead.get("email") if lead.get("email") != "—" else "",
        "seller_phone": lead.get("phone") if lead.get("phone") != "—" else "",
        "owner_first_name": fn,
        "owner_last_name": ln,
        "owner_email": lead.get("email") if lead.get("email") != "—" else "",
        "owner_phone": lead.get("phone") if lead.get("phone") != "—" else "",
        "private_seller": "Oui" if is_particulier else "Non",
        "first_listed_date": (lead.get("published_at") or "")[:10] or "",
        "first_sale_date": (lead.get("published_at") or "")[:10] or "",
        "days_on_market": days if days is not None else "",
        "previous_price": prev_price or "",
        "price_drop_count": "1" if prev_price and price and prev_price > price else "",
        "recent_price_drop": "Oui" if "baisse_prix" in (lead.get("alert_tags") or []) else "",
        "recent_price_drop_pct": abs(drop_pct) if drop_pct and drop_pct < 0 else (drop_pct or ""),
        "long_listing": _long_listing_label(days),
        "market_estimate": lead.get("dvf_median_m2") and lead.get("surface")
        and int(lead["dvf_median_m2"] * lead["surface"])
        or "",
        "portal_listings": lead.get("source") or "",
    }
    if mandate_type == "location":
        common.update({
            "rent_cc": lead.get("price") if lead.get("transaction_type") == "location" else "",
            "surface": lead.get("surface") or "",
            "long_vacancy": "Oui" if days and days > 60 else "",
        })
    else:
        common.update({
            "price_fai": lead.get("price") or "",
            "surface_carrez": lead.get("surface") or "",
        })
    return common


def _row_mandate(row) -> dict:
    return {
        "id": row["id"],
        "agency_id": row["agency_id"],
        "lead_id": row["lead_id"],
        "mandate_type": row["mandate_type"],
        "exclusivity": row["exclusivity"],
        "status": row["status"],
        "title": row["title"],
        "fields": json.loads(row["fields_json"] or "{}"),
        "body_html": row["body_html"],
        "recipient_email": row["recipient_email"],
        "sent_at": row["sent_at"],
        "signed_at": row["signed_at"],
        "notes": row["notes"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


def list_seller_mandates(
    agency_id: str,
    *,
    mandate_type: str | None = None,
    status: str | None = None,
    lead_id: int | None = None,
) -> list[dict]:
    q = "SELECT * FROM seller_mandates WHERE agency_id = ?"
    params: list = [agency_id]
    if mandate_type:
        q += " AND mandate_type = ?"
        params.append(mandate_type)
    if status:
        q += " AND status = ?"
        params.append(status)
    if lead_id is not None:
        q += " AND lead_id = ?"
        params.append(lead_id)
    q += " ORDER BY updated_at DESC"
    with get_connection() as conn:
        ensure_mandate_tables(conn)
        rows = conn.execute(q, params).fetchall()
    return [_row_mandate(r) for r in rows]


def get_seller_mandate(mandate_id: str, agency_id: str) -> dict | None:
    with get_connection() as conn:
        ensure_mandate_tables(conn)
        row = conn.execute(
            "SELECT * FROM seller_mandates WHERE id = ? AND agency_id = ?",
            (mandate_id, agency_id),
        ).fetchone()
    return _row_mandate(row) if row else None


def create_seller_mandate(
    agency_id: str,
    *,
    mandate_type: str,
    lead_id: int | None = None,
    exclusivity: str = "exclusif",
    fields: dict | None = None,
    title: str | None = None,
) -> dict:
    meta = get_template_meta(mandate_type)
    merged = dict(fields or {})
    if lead_id:
        lead = get_lead(lead_id, agency_id)
        if lead:
            merged = {**fields_from_lead(lead, mandate_type), **merged}
    profile = get_agency_legal_profile(agency_id)
    body = render_mandate_html(mandate_type, exclusivity, merged, profile)
    mid = str(uuid.uuid4())
    now = _now()
    tit = title or f"{meta['title']} — {merged.get('property_address') or 'Bien'}"
    with get_connection() as conn:
        ensure_mandate_tables(conn)
        conn.execute(
            """INSERT INTO seller_mandates
               (id, agency_id, lead_id, mandate_type, exclusivity, status, title,
                fields_json, body_html, recipient_email, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, 'draft', ?, ?, ?, ?, ?, ?)""",
            (
                mid,
                agency_id,
                lead_id,
                mandate_type,
                exclusivity,
                tit,
                json.dumps(merged, ensure_ascii=False),
                body,
                merged.get("seller_email") or merged.get("owner_email"),
                now,
                now,
            ),
        )
        conn.commit()
    return get_seller_mandate(mid, agency_id)


def update_seller_mandate(mandate_id: str, agency_id: str, data: dict) -> dict | None:
    row = get_seller_mandate(mandate_id, agency_id)
    if not row:
        return None
    fields = {**row["fields"], **(data.get("fields") or {})}
    mandate_type = data.get("mandate_type") or row["mandate_type"]
    exclusivity = data.get("exclusivity") or row["exclusivity"]
    profile = get_agency_legal_profile(agency_id)
    body = render_mandate_html(mandate_type, exclusivity, fields, profile)
    title = data.get("title") or row["title"]
    status = data.get("status") or row["status"]
    recipient = data.get("recipient_email", row.get("recipient_email"))
    notes = data.get("notes", row.get("notes"))
    sent_at = row["sent_at"]
    if data.get("mark_sent"):
        status = "sent"
        sent_at = _now()
    signed_at = row.get("signed_at")
    if data.get("mark_signed"):
        status = "signed"
        signed_at = _now()
    now = _now()
    with get_connection() as conn:
        ensure_mandate_tables(conn)
        conn.execute(
            """UPDATE seller_mandates SET
               mandate_type = ?, exclusivity = ?, status = ?, title = ?,
               fields_json = ?, body_html = ?, recipient_email = ?,
               sent_at = ?, signed_at = ?, notes = ?, updated_at = ?
               WHERE id = ? AND agency_id = ?""",
            (
                mandate_type,
                exclusivity,
                status,
                title,
                json.dumps(fields, ensure_ascii=False),
                body,
                recipient,
                sent_at,
                signed_at,
                notes,
                now,
                mandate_id,
                agency_id,
            ),
        )
        conn.commit()
    return get_seller_mandate(mandate_id, agency_id)


def delete_seller_mandate(mandate_id: str, agency_id: str) -> bool:
    from crm.mandates.dossiers import delete_dossiers_for_mandate

    delete_dossiers_for_mandate(mandate_id, agency_id)
    with get_connection() as conn:
        ensure_mandate_tables(conn)
        cur = conn.execute(
            "DELETE FROM seller_mandates WHERE id = ? AND agency_id = ?",
            (mandate_id, agency_id),
        )
        conn.commit()
        return cur.rowcount > 0


# ─── Acheteurs / Locataires ───

CLIENT_SEGMENTS = ("acheteur", "locataire")


def _row_client(row) -> dict:
    return {
        "id": row["id"],
        "agency_id": row["agency_id"],
        "segment": row["segment"],
        "first_name": row["first_name"],
        "last_name": row["last_name"],
        "phone": row["phone"],
        "email": row["email"],
        "budget_min": row["budget_min"],
        "budget_max": row["budget_max"],
        "property_type": row["property_type"],
        "rooms_min": row["rooms_min"],
        "surface_min": row["surface_min"],
        "cities": json.loads(row["cities_json"] or "[]"),
        "status": row["status"],
        "notes": row["notes"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
        "full_name": " ".join(
            p for p in (row["first_name"], row["last_name"]) if p
        ).strip()
    }


def list_property_clients(
    agency_id: str,
    *,
    segment: str | None = None,
) -> list[dict]:
    q = "SELECT * FROM property_clients WHERE agency_id = ?"
    params: list = [agency_id]
    if segment:
        q += " AND segment = ?"
        params.append(segment)
    q += " ORDER BY updated_at DESC"
    with get_connection() as conn:
        ensure_mandate_tables(conn)
        rows = conn.execute(q, params).fetchall()
    return [_row_client(r) for r in rows]


def get_property_client(client_id: str, agency_id: str) -> dict | None:
    with get_connection() as conn:
        ensure_mandate_tables(conn)
        row = conn.execute(
            "SELECT * FROM property_clients WHERE id = ? AND agency_id = ?",
            (client_id, agency_id),
        ).fetchone()
    return _row_client(row) if row else None


def create_property_client(agency_id: str, data: dict) -> dict:
    segment = (data.get("segment") or "acheteur").lower()
    if segment not in CLIENT_SEGMENTS:
        raise ValueError("Segment : acheteur ou locataire")
    cid = str(uuid.uuid4())
    now = _now()
    cities = data.get("cities") or []
    with get_connection() as conn:
        ensure_mandate_tables(conn)
        conn.execute(
            """INSERT INTO property_clients
               (id, agency_id, segment, first_name, last_name, phone, email,
                budget_min, budget_max, property_type, rooms_min, surface_min,
                cities_json, status, notes, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                cid,
                agency_id,
                segment,
                (data.get("first_name") or "").strip(),
                (data.get("last_name") or "").strip(),
                (data.get("phone") or "").strip(),
                (data.get("email") or "").strip().lower(),
                data.get("budget_min"),
                data.get("budget_max"),
                (data.get("property_type") or "").strip(),
                data.get("rooms_min"),
                data.get("surface_min"),
                json.dumps(cities if isinstance(cities, list) else []),
                (data.get("status") or "actif").strip(),
                (data.get("notes") or "").strip(),
                now,
                now,
            ),
        )
        conn.commit()
    return get_property_client(cid, agency_id)


def update_property_client(client_id: str, agency_id: str, data: dict) -> dict | None:
    row = get_property_client(client_id, agency_id)
    if not row:
        return None
    segment = (data.get("segment") or row["segment"]).lower()
    cities = data.get("cities", row["cities"])
    now = _now()
    with get_connection() as conn:
        ensure_mandate_tables(conn)
        conn.execute(
            """UPDATE property_clients SET
               segment = ?, first_name = ?, last_name = ?, phone = ?, email = ?,
               budget_min = ?, budget_max = ?, property_type = ?, rooms_min = ?,
               surface_min = ?, cities_json = ?, status = ?, notes = ?, updated_at = ?
               WHERE id = ? AND agency_id = ?""",
            (
                segment,
                data.get("first_name", row["first_name"]),
                data.get("last_name", row["last_name"]),
                data.get("phone", row["phone"]),
                data.get("email", row["email"]),
                data.get("budget_min", row["budget_min"]),
                data.get("budget_max", row["budget_max"]),
                data.get("property_type", row["property_type"]),
                data.get("rooms_min", row["rooms_min"]),
                data.get("surface_min", row["surface_min"]),
                json.dumps(cities if isinstance(cities, list) else []),
                data.get("status", row["status"]),
                data.get("notes", row["notes"]),
                now,
                client_id,
                agency_id,
            ),
        )
        conn.commit()
    return get_property_client(client_id, agency_id)


def delete_property_client(client_id: str, agency_id: str) -> bool:
    with get_connection() as conn:
        ensure_mandate_tables(conn)
        cur = conn.execute(
            "DELETE FROM property_clients WHERE id = ? AND agency_id = ?",
            (client_id, agency_id),
        )
        conn.commit()
        return cur.rowcount > 0
