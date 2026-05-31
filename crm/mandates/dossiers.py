"""Dossiers commercialisation liés aux mandats — photos et clients ciblés."""

from __future__ import annotations

import json
import shutil
import uuid
from datetime import datetime, timezone
from pathlib import Path

from crawler.storage import get_connection

ALLOWED_PHOTO_EXT = frozenset({".jpg", ".jpeg", ".png", ".webp", ".gif"})
MAX_PHOTO_BYTES = 8 * 1024 * 1024

_UPLOAD_ROOT = Path(__file__).resolve().parent.parent.parent / "data" / "uploads" / "mandate-dossiers"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def upload_root() -> Path:
    return _UPLOAD_ROOT


def ensure_dossier_tables(conn) -> None:
    conn.execute("""
        CREATE TABLE IF NOT EXISTS mandate_dossiers (
            id TEXT PRIMARY KEY,
            agency_id TEXT NOT NULL,
            mandate_id TEXT NOT NULL,
            title TEXT NOT NULL,
            description TEXT,
            property_address TEXT,
            postal_code TEXT,
            city TEXT,
            surface REAL,
            rooms TEXT,
            price INTEGER,
            property_type TEXT,
            photos_json TEXT NOT NULL DEFAULT '[]',
            linked_clients_json TEXT NOT NULL DEFAULT '[]',
            status TEXT NOT NULL DEFAULT 'actif',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
    """)
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_mandate_dossiers_mandate "
        "ON mandate_dossiers(mandate_id, agency_id)"
    )


def _dossier_dir(agency_id: str, dossier_id: str) -> Path:
    return _UPLOAD_ROOT / agency_id / dossier_id


def _row_dossier(row) -> dict:
    return {
        "id": row["id"],
        "agency_id": row["agency_id"],
        "mandate_id": row["mandate_id"],
        "title": row["title"],
        "description": row["description"] or "",
        "property_address": row["property_address"] or "",
        "postal_code": row["postal_code"] or "",
        "city": row["city"] or "",
        "surface": row["surface"],
        "rooms": row["rooms"] or "",
        "price": row["price"],
        "property_type": row["property_type"] or "",
        "photos": json.loads(row["photos_json"] or "[]"),
        "linked_clients": json.loads(row["linked_clients_json"] or "[]"),
        "status": row["status"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


def dossier_from_mandate_fields(mandate: dict) -> dict:
    """Préremplit un dossier depuis les champs du mandat."""
    f = mandate.get("fields") or {}
    mt = mandate.get("mandate_type") or "vente"
    surface = f.get("surface_carrez") or f.get("surface")
    price = f.get("price_fai") or f.get("rent_cc") or f.get("rent_hc")
    addr = f.get("property_address") or mandate.get("title") or "Bien"
    return {
        "title": addr,
        "description": "",
        "property_address": f.get("property_address") or "",
        "postal_code": f.get("postal_code") or "",
        "city": f.get("city") or "",
        "surface": float(surface) if surface not in (None, "") else None,
        "rooms": str(f.get("rooms") or ""),
        "price": int(float(price)) if price not in (None, "") else None,
        "property_type": f.get("property_type") or "",
    }


def list_mandate_dossiers(mandate_id: str, agency_id: str) -> list[dict]:
    with get_connection() as conn:
        ensure_dossier_tables(conn)
        rows = conn.execute(
            """SELECT * FROM mandate_dossiers
               WHERE mandate_id = ? AND agency_id = ?
               ORDER BY updated_at DESC""",
            (mandate_id, agency_id),
        ).fetchall()
    return [_row_dossier(r) for r in rows]


def get_mandate_dossier(dossier_id: str, agency_id: str) -> dict | None:
    with get_connection() as conn:
        ensure_dossier_tables(conn)
        row = conn.execute(
            "SELECT * FROM mandate_dossiers WHERE id = ? AND agency_id = ?",
            (dossier_id, agency_id),
        ).fetchone()
    return _row_dossier(row) if row else None


def create_mandate_dossier(agency_id: str, mandate_id: str, data: dict) -> dict:
    did = str(uuid.uuid4())
    now = _now()
    with get_connection() as conn:
        ensure_dossier_tables(conn)
        conn.execute(
            """INSERT INTO mandate_dossiers
               (id, agency_id, mandate_id, title, description,
                property_address, postal_code, city, surface, rooms, price,
                property_type, photos_json, linked_clients_json, status,
                created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, '[]', '[]', ?, ?, ?)""",
            (
                did,
                agency_id,
                mandate_id,
                (data.get("title") or "Dossier bien").strip(),
                (data.get("description") or "").strip(),
                (data.get("property_address") or "").strip(),
                (data.get("postal_code") or "").strip(),
                (data.get("city") or "").strip(),
                data.get("surface"),
                str(data.get("rooms") or "").strip(),
                data.get("price"),
                (data.get("property_type") or "").strip(),
                (data.get("status") or "actif").strip(),
                now,
                now,
            ),
        )
        conn.commit()
    _dossier_dir(agency_id, did).mkdir(parents=True, exist_ok=True)
    return get_mandate_dossier(did, agency_id)


def update_mandate_dossier(dossier_id: str, agency_id: str, data: dict) -> dict | None:
    row = get_mandate_dossier(dossier_id, agency_id)
    if not row:
        return None
    fields = {
        "title": data.get("title", row["title"]),
        "description": data.get("description", row["description"]),
        "property_address": data.get("property_address", row["property_address"]),
        "postal_code": data.get("postal_code", row["postal_code"]),
        "city": data.get("city", row["city"]),
        "surface": data.get("surface", row["surface"]),
        "rooms": data.get("rooms", row["rooms"]),
        "price": data.get("price", row["price"]),
        "property_type": data.get("property_type", row["property_type"]),
        "status": data.get("status", row["status"]),
    }
    linked = data.get("linked_clients")
    photos_json = json.dumps(row["photos"], ensure_ascii=False)
    linked_json = (
        json.dumps(linked, ensure_ascii=False)
        if linked is not None
        else json.dumps(row["linked_clients"], ensure_ascii=False)
    )
    now = _now()
    with get_connection() as conn:
        conn.execute(
            """UPDATE mandate_dossiers SET
               title = ?, description = ?, property_address = ?, postal_code = ?,
               city = ?, surface = ?, rooms = ?, price = ?, property_type = ?,
               linked_clients_json = ?, status = ?, updated_at = ?
               WHERE id = ? AND agency_id = ?""",
            (
                str(fields["title"] or "").strip(),
                str(fields["description"] or "").strip(),
                str(fields["property_address"] or "").strip(),
                str(fields["postal_code"] or "").strip(),
                str(fields["city"] or "").strip(),
                fields["surface"],
                str(fields["rooms"] or "").strip(),
                fields["price"],
                str(fields["property_type"] or "").strip(),
                linked_json,
                str(fields["status"] or "actif").strip(),
                now,
                dossier_id,
                agency_id,
            ),
        )
        conn.commit()
    return get_mandate_dossier(dossier_id, agency_id)


def delete_mandate_dossier(dossier_id: str, agency_id: str) -> bool:
    row = get_mandate_dossier(dossier_id, agency_id)
    if not row:
        return False
    with get_connection() as conn:
        conn.execute(
            "DELETE FROM mandate_dossiers WHERE id = ? AND agency_id = ?",
            (dossier_id, agency_id),
        )
        conn.commit()
    folder = _dossier_dir(agency_id, dossier_id)
    if folder.is_dir():
        shutil.rmtree(folder, ignore_errors=True)
    return True


def delete_dossiers_for_mandate(mandate_id: str, agency_id: str) -> None:
    for d in list_mandate_dossiers(mandate_id, agency_id):
        delete_mandate_dossier(d["id"], agency_id)


def photo_public_url(agency_id: str, dossier_id: str, filename: str) -> str:
    return f"/api/mandates/dossier-files/{agency_id}/{dossier_id}/{filename}"


def add_dossier_photo(
    dossier_id: str,
    agency_id: str,
    filename: str,
    raw: bytes,
    *,
    caption: str = "",
) -> dict | None:
    row = get_mandate_dossier(dossier_id, agency_id)
    if not row:
        return None
    ext = Path(filename).suffix.lower()
    if ext not in ALLOWED_PHOTO_EXT:
        raise ValueError("Format photo : JPG, PNG ou WebP")
    if len(raw) > MAX_PHOTO_BYTES:
        raise ValueError("Photo trop lourde (max 8 Mo)")

    pid = str(uuid.uuid4())
    safe_name = f"{pid}{ext}"
    folder = _dossier_dir(agency_id, dossier_id)
    folder.mkdir(parents=True, exist_ok=True)
    (folder / safe_name).write_bytes(raw)

    photo = {
        "id": pid,
        "filename": safe_name,
        "url": photo_public_url(agency_id, dossier_id, safe_name),
        "caption": (caption or "").strip(),
        "created_at": _now(),
    }
    photos = [*row["photos"], photo]
    now = _now()
    with get_connection() as conn:
        conn.execute(
            """UPDATE mandate_dossiers SET photos_json = ?, updated_at = ?
               WHERE id = ? AND agency_id = ?""",
            (json.dumps(photos, ensure_ascii=False), now, dossier_id, agency_id),
        )
        conn.commit()
    return get_mandate_dossier(dossier_id, agency_id)


def remove_dossier_photo(dossier_id: str, agency_id: str, photo_id: str) -> dict | None:
    row = get_mandate_dossier(dossier_id, agency_id)
    if not row:
        return None
    kept = []
    removed = None
    for p in row["photos"]:
        if p.get("id") == photo_id:
            removed = p
        else:
            kept.append(p)
    if not removed:
        return row
    path = _dossier_dir(agency_id, dossier_id) / removed.get("filename", "")
    if path.is_file():
        path.unlink(missing_ok=True)
    now = _now()
    with get_connection() as conn:
        conn.execute(
            """UPDATE mandate_dossiers SET photos_json = ?, updated_at = ?
               WHERE id = ? AND agency_id = ?""",
            (json.dumps(kept, ensure_ascii=False), now, dossier_id, agency_id),
        )
        conn.commit()
    return get_mandate_dossier(dossier_id, agency_id)


def link_client_to_dossier(
    dossier_id: str,
    agency_id: str,
    client_id: str,
    *,
    notes: str = "",
) -> dict | None:
    from crm.mandates.storage import get_property_client

    if not get_property_client(client_id, agency_id):
        raise ValueError("Fiche client introuvable")
    row = get_mandate_dossier(dossier_id, agency_id)
    if not row:
        return None
    linked = list(row["linked_clients"])
    if not any(x.get("client_id") == client_id for x in linked):
        linked.append({
            "client_id": client_id,
            "proposed_at": _now(),
            "notes": (notes or "").strip(),
        })
    return update_mandate_dossier(dossier_id, agency_id, {"linked_clients": linked})


def unlink_client_from_dossier(
    dossier_id: str,
    agency_id: str,
    client_id: str,
) -> dict | None:
    row = get_mandate_dossier(dossier_id, agency_id)
    if not row:
        return None
    linked = [x for x in row["linked_clients"] if x.get("client_id") != client_id]
    return update_mandate_dossier(dossier_id, agency_id, {"linked_clients": linked})


def resolve_dossier_photo_path(agency_id: str, dossier_id: str, filename: str) -> Path | None:
    if ".." in filename or "/" in filename or "\\" in filename:
        return None
    row = get_mandate_dossier(dossier_id, agency_id)
    if not row:
        return None
    allowed = {p.get("filename") for p in row["photos"]}
    if filename not in allowed:
        return None
    path = _dossier_dir(agency_id, dossier_id) / filename
    return path if path.is_file() else None
