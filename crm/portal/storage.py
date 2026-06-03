"""Persistance annonces portail Veliora."""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone

from crawler.storage import get_agency_name, get_connection

PORTAL_SOURCE_ID = "veliora_portail"
LISTING_STATUSES = ("draft", "pending", "published", "archived")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def ensure_portal_tables(conn) -> None:
    conn.execute("""
        CREATE TABLE IF NOT EXISTS portal_listings (
            id TEXT PRIMARY KEY,
            agency_id TEXT,
            publisher_type TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending',
            transaction_type TEXT NOT NULL DEFAULT 'vente',
            title TEXT NOT NULL,
            description TEXT,
            property_type TEXT,
            price INTEGER,
            surface REAL,
            rooms INTEGER,
            city TEXT NOT NULL,
            postcode TEXT,
            address TEXT,
            contact_name TEXT,
            contact_phone TEXT,
            contact_email TEXT,
            image_url TEXT,
            agent_id TEXT,
            agent_name TEXT,
            source_lead_id INTEGER,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            published_at TEXT
        )
    """)
    # Migration douce des tables créées avant l'ajout des colonnes agent.
    from velora_db.introspect import ensure_columns

    ensure_columns(
        conn,
        "portal_listings",
        {
            "agent_id": "TEXT",
            "agent_name": "TEXT",
            "source_lead_id": "INTEGER",
        },
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_portal_listings_status "
        "ON portal_listings(status, city)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_portal_listings_agency "
        "ON portal_listings(agency_id, status)"
    )


def _row_to_listing(row) -> dict:
    if not row:
        return {}
    d = dict(row)
    for k in ("price", "surface", "rooms"):
        if d.get(k) is not None:
            try:
                if k == "surface":
                    d[k] = float(d[k])
                elif k == "rooms":
                    d[k] = int(d[k])
                else:
                    d[k] = int(d[k])
            except (TypeError, ValueError):
                pass
    if d.get("agency_id"):
        d["agency_name"] = get_agency_name(d["agency_id"]) or d["agency_id"]
    return d


def list_listings(
    *,
    agency_id: str | None = None,
    status: str | None = None,
    city: str | None = None,
    transaction_type: str | None = None,
    publisher_type: str | None = None,
    public_only: bool = False,
    limit: int = 100,
) -> list[dict]:
    clauses = ["1=1"]
    params: list = []
    if public_only:
        clauses.append("status = ?")
        params.append("published")
    elif status:
        clauses.append("status = ?")
        params.append(status)
    if agency_id:
        clauses.append("agency_id = ?")
        params.append(agency_id)
    if city:
        clauses.append("LOWER(city) LIKE ?")
        params.append(f"%{city.strip().lower()}%")
    if transaction_type:
        clauses.append("transaction_type = ?")
        params.append(transaction_type.strip().lower())
    if publisher_type:
        clauses.append("publisher_type = ?")
        params.append(publisher_type.strip().lower())
    sql = (
        f"SELECT * FROM portal_listings WHERE {' AND '.join(clauses)} "
        f"ORDER BY COALESCE(published_at, created_at) DESC LIMIT ?"
    )
    params.append(max(1, min(limit, 200)))
    with get_connection() as conn:
        ensure_portal_tables(conn)
        rows = conn.execute(sql, params).fetchall()
    return [_row_to_listing(r) for r in rows]


def get_listing(listing_id: str, *, agency_id: str | None = None, public: bool = False) -> dict | None:
    with get_connection() as conn:
        ensure_portal_tables(conn)
        row = conn.execute(
            "SELECT * FROM portal_listings WHERE id = ?",
            (listing_id,),
        ).fetchone()
    if not row:
        return None
    item = _row_to_listing(row)
    if public:
        if item.get("status") != "published":
            return None
        if (item.get("publisher_type") or "").lower() != "agency":
            return None
    if agency_id and item.get("agency_id") != agency_id:
        return None
    return item


def create_listing(data: dict, *, agency_id: str | None, publisher_type: str) -> dict:
    lid = uuid.uuid4().hex
    now = _now()
    status = (data.get("status") or "pending").strip().lower()
    if status not in LISTING_STATUSES:
        status = "pending"
    if publisher_type == "agency" and agency_id:
        status = data.get("status") or "published"
    published_at = now if status == "published" else None
    with get_connection() as conn:
        ensure_portal_tables(conn)
        conn.execute(
            """INSERT INTO portal_listings
               (id, agency_id, publisher_type, status, transaction_type, title, description,
                property_type, price, surface, rooms, city, postcode, address,
                contact_name, contact_phone, contact_email, image_url,
                agent_id, agent_name, source_lead_id,
                created_at, updated_at, published_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                lid,
                agency_id,
                publisher_type,
                status,
                (data.get("transaction_type") or "vente").strip().lower(),
                (data.get("title") or "").strip(),
                (data.get("description") or "").strip(),
                (data.get("property_type") or "appartement").strip().lower(),
                data.get("price"),
                data.get("surface"),
                data.get("rooms"),
                (data.get("city") or "").strip(),
                (data.get("postcode") or "").strip() or None,
                (data.get("address") or "").strip() or None,
                (data.get("contact_name") or "").strip() or None,
                (data.get("contact_phone") or "").strip() or None,
                (data.get("contact_email") or "").strip().lower() or None,
                (data.get("image_url") or "").strip() or None,
                (data.get("agent_id") or "").strip() or None,
                (data.get("agent_name") or "").strip() or None,
                data.get("source_lead_id"),
                now,
                now,
                published_at,
            ),
        )
        conn.commit()
    return get_listing(lid, agency_id=agency_id) or {}


def update_listing(listing_id: str, agency_id: str, data: dict) -> dict | None:
    existing = get_listing(listing_id, agency_id=agency_id)
    if not existing:
        return None
    now = _now()
    fields = {
        "title": (data.get("title") or existing.get("title") or "").strip(),
        "description": (data.get("description") or existing.get("description") or "").strip(),
        "transaction_type": (data.get("transaction_type") or existing.get("transaction_type") or "vente").strip().lower(),
        "property_type": (data.get("property_type") or existing.get("property_type") or "appartement").strip().lower(),
        "price": data.get("price") if "price" in data else existing.get("price"),
        "surface": data.get("surface") if "surface" in data else existing.get("surface"),
        "rooms": data.get("rooms") if "rooms" in data else existing.get("rooms"),
        "city": (data.get("city") or existing.get("city") or "").strip(),
        "postcode": (data.get("postcode") or existing.get("postcode") or "").strip() or None,
        "address": (data.get("address") or existing.get("address") or "").strip() or None,
        "contact_name": (data.get("contact_name") or existing.get("contact_name") or "").strip() or None,
        "contact_phone": (data.get("contact_phone") or existing.get("contact_phone") or "").strip() or None,
        "contact_email": (data.get("contact_email") or existing.get("contact_email") or "").strip().lower() or None,
        "image_url": (data.get("image_url") or existing.get("image_url") or "").strip() or None,
        "status": (data.get("status") or existing.get("status") or "draft").strip().lower(),
    }
    published_at = existing.get("published_at")
    if fields["status"] == "published" and not published_at:
        published_at = now
    with get_connection() as conn:
        ensure_portal_tables(conn)
        conn.execute(
            """UPDATE portal_listings SET
               title=?, description=?, transaction_type=?, property_type=?,
               price=?, surface=?, rooms=?, city=?, postcode=?, address=?,
               contact_name=?, contact_phone=?, contact_email=?, image_url=?,
               status=?, updated_at=?, published_at=?
               WHERE id=? AND agency_id=?""",
            (
                fields["title"],
                fields["description"],
                fields["transaction_type"],
                fields["property_type"],
                fields["price"],
                fields["surface"],
                fields["rooms"],
                fields["city"],
                fields["postcode"],
                fields["address"],
                fields["contact_name"],
                fields["contact_phone"],
                fields["contact_email"],
                fields["image_url"],
                fields["status"],
                now,
                published_at,
                listing_id,
                agency_id,
            ),
        )
        conn.commit()
    return get_listing(listing_id, agency_id=agency_id)


def delete_listing(listing_id: str, agency_id: str) -> bool:
    with get_connection() as conn:
        ensure_portal_tables(conn)
        cur = conn.execute(
            "DELETE FROM portal_listings WHERE id = ? AND agency_id = ?",
            (listing_id, agency_id),
        )
        conn.commit()
        return cur.rowcount > 0


def public_listing_payload(item: dict) -> dict:
    """Réponse API publique — masque certains contacts si besoin."""
    return {
        "id": item.get("id"),
        "publisher_type": item.get("publisher_type"),
        "transaction_type": item.get("transaction_type"),
        "title": item.get("title"),
        "description": item.get("description"),
        "property_type": item.get("property_type"),
        "price": item.get("price"),
        "surface": item.get("surface"),
        "rooms": item.get("rooms"),
        "city": item.get("city"),
        "postcode": item.get("postcode"),
        "address": item.get("address"),
        "image_url": item.get("image_url"),
        "agency_name": item.get("agency_name"),
        "published_at": item.get("published_at"),
        "created_at": item.get("created_at"),
    }
