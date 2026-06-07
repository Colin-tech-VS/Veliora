"""Dossiers commercialisation liés aux mandats — photos et clients ciblés."""

from __future__ import annotations

import json
import shutil
import uuid
from datetime import datetime, timezone
from pathlib import Path

from crawler.storage import get_connection
from velora_db.introspect import table_column_names

ALLOWED_PHOTO_EXT = frozenset({".jpg", ".jpeg", ".png", ".webp", ".gif"})
MAX_PHOTO_BYTES = 8 * 1024 * 1024

# Documents (espace type Drive) : tous formats courants, plus lourds que les photos.
ALLOWED_DOC_EXT = frozenset({
    ".pdf", ".jpg", ".jpeg", ".png", ".webp", ".gif", ".heic", ".heif",
    ".doc", ".docx", ".odt", ".rtf", ".txt",
    ".xls", ".xlsx", ".ods", ".csv",
    ".ppt", ".pptx",
    ".zip",
})
MAX_DOC_BYTES = 25 * 1024 * 1024

_UPLOAD_ROOT = Path(__file__).resolve().parent.parent.parent / "data" / "uploads" / "mandate-dossiers"


# ── Pièces à fournir : checklist générée automatiquement selon le profil ────
#
# Chaque entrée décrit un « dossier » (au sens Drive) que l'agent doit remplir.
# `required` peut dépendre du profil vendeur → calculé dans build_document_checklist.

_CHECKLIST_BASE = [
    {"key": "identite", "name": "Pièce d'identité",
     "description": "Carte nationale d'identité, passeport ou titre de séjour en cours de validité."},
    {"key": "domicile", "name": "Justificatif de domicile",
     "description": "Facture récente (énergie, téléphone, etc.) de moins de 3 mois."},
    {"key": "titre_propriete", "name": "Titre de propriété",
     "description": "Acte notarié d'acquisition du bien."},
    {"key": "diagnostics", "name": "Diagnostics immobiliers obligatoires",
     "description": "DPE, amiante, plomb, électricité, gaz, ERP… (ou informations permettant de les réaliser)."},
    {"key": "taxe_fonciere", "name": "Dernier avis de taxe foncière",
     "description": "Avis de taxe foncière le plus récent."},
    {"key": "rib", "name": "Relevé d'identité bancaire (RIB)",
     "description": "Parfois demandé, notamment si des fonds doivent être versés ultérieurement.",
     "optional": True},
]

_CHECKLIST_COPRO = [
    {"key": "copropriete", "name": "Documents de copropriété",
     "description": "Règlement de copropriété, procès-verbaux d'assemblée générale, montant des charges, etc."},
]

_CHECKLIST_INDIVISION = [
    {"key": "identites_indivisaires", "name": "Pièces d'identité de tous les propriétaires",
     "description": "Une pièce d'identité valide pour chaque indivisaire / copropriétaire / époux concerné."},
    {"key": "accord_indivisaires", "name": "Accord de tous les indivisaires",
     "description": "Accord ou signature de tous les indivisaires, époux ou copropriétaires concernés."},
]

_CHECKLIST_SOCIETE = [
    {"key": "kbis", "name": "Extrait Kbis récent",
     "description": "Extrait Kbis de moins de 3 mois."},
    {"key": "statuts", "name": "Statuts de la société",
     "description": "Statuts à jour de la société."},
    {"key": "identite_representant", "name": "Pièce d'identité du représentant légal",
     "description": "Pièce d'identité valide du représentant légal signataire."},
    {"key": "pouvoirs", "name": "Pouvoirs de signature",
     "description": "Document justifiant les pouvoirs de signature du représentant, si nécessaire.",
     "optional": True},
]

# Mentions obligatoires que l'agent doit faire figurer dans le mandat écrit.
MANDATE_MENTIONS = [
    "Identité de l'agent immobilier et numéro de carte professionnelle.",
    "Objet du mandat.",
    "Durée du mandat.",
    "Conditions de rémunération (honoraires).",
    "Modalités de résiliation.",
    "Numéro d'inscription du mandat dans le registre des mandats.",
]


def _is_yes(value) -> bool:
    return str(value or "").strip().lower() in {"oui", "yes", "true", "1", "o"}


def detect_seller_profile(mandate: dict | None) -> dict:
    """Déduit le profil vendeur des champs du mandat pour piloter la checklist."""
    f = (mandate or {}).get("fields") or {}
    status = str(f.get("owner_legal_status") or f.get("owner_type") or "Particulier").strip()
    try:
        owner_count = int(float(f.get("owner_count") or 1))
    except (TypeError, ValueError):
        owner_count = 1
    is_societe = status.lower() in {"sci", "société", "societe", "sas", "sarl", "sa"}
    is_copro = _is_yes(f.get("is_copro")) or str(
        f.get("property_type") or ""
    ).strip().lower() in {"appartement", "studio", "loft", "duplex"}
    is_indivision = owner_count > 1
    labels = []
    if is_societe:
        labels.append("Société")
    elif is_indivision:
        labels.append("Indivision / plusieurs propriétaires")
    else:
        labels.append("Propriétaire vendeur particulier")
    if is_copro:
        labels.append("Bien en copropriété")
    return {
        "status": status,
        "owner_count": owner_count,
        "is_societe": is_societe,
        "is_copro": is_copro,
        "is_indivision": is_indivision,
        "label": " · ".join(labels),
    }


def build_document_checklist(mandate: dict | None) -> list[dict]:
    """Liste ordonnée des pièces à fournir, adaptée au profil du vendeur."""
    profile = detect_seller_profile(mandate)
    items: list[dict] = list(_CHECKLIST_BASE)
    if profile["is_copro"]:
        items += _CHECKLIST_COPRO
    if profile["is_indivision"]:
        items += _CHECKLIST_INDIVISION
    if profile["is_societe"]:
        items += _CHECKLIST_SOCIETE
    out = []
    for it in items:
        out.append({
            "key": it["key"],
            "name": it["name"],
            "description": it.get("description", ""),
            "required": not it.get("optional", False),
            "auto": True,
        })
    return out


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _row_get(row, key: str, default=None):
    """Accès tolérant à une colonne sqlite.Row potentiellement absente."""
    try:
        keys = row.keys()
    except AttributeError:
        return default
    return row[key] if key in keys else default


def upload_root() -> Path:
    return _UPLOAD_ROOT


def ensure_dossier_tables(conn) -> None:
    conn.execute("""
        CREATE TABLE IF NOT EXISTS mandate_dossiers (
            id TEXT PRIMARY KEY,
            agency_id TEXT NOT NULL,
            mandate_id TEXT NOT NULL,
            lead_id INTEGER,
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
            documents_json TEXT NOT NULL DEFAULT '{}',
            status TEXT NOT NULL DEFAULT 'actif',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
    """)
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_mandate_dossiers_mandate "
        "ON mandate_dossiers(mandate_id, agency_id)"
    )
    # Migration : colonne documents_json sur les tables existantes.
    # PRAGMA n'existe pas sous PostgreSQL → introspection portable.
    cols = table_column_names(conn, "mandate_dossiers")
    if "documents_json" not in cols:
        conn.execute(
            "ALTER TABLE mandate_dossiers ADD COLUMN documents_json TEXT NOT NULL DEFAULT '{}'"
        )
    if "lead_id" not in cols:
        conn.execute("ALTER TABLE mandate_dossiers ADD COLUMN lead_id INTEGER")
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_mandate_dossiers_lead "
        "ON mandate_dossiers(agency_id, lead_id)"
    )


def _dossier_dir(agency_id: str, dossier_id: str) -> Path:
    return _UPLOAD_ROOT / agency_id / dossier_id


def _row_dossier(row) -> dict:
    return {
        "id": row["id"],
        "agency_id": row["agency_id"],
        "mandate_id": row["mandate_id"],
        "lead_id": _row_get(row, "lead_id"),
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
        "documents": json.loads(_row_get(row, "documents_json") or "{}"),
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
        lead_id = data.get("lead_id")
        conn.execute(
            """INSERT INTO mandate_dossiers
               (id, agency_id, mandate_id, lead_id, title, description,
                property_address, postal_code, city, surface, rooms, price,
                property_type, photos_json, linked_clients_json, status,
                created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, '[]', '[]', ?, ?, ?)""",
            (
                did,
                agency_id,
                mandate_id,
                int(lead_id) if lead_id not in (None, "") else None,
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


# ── Dossier auto-créé dès la prise en charge d'une annonce par un agent ──────
#
# Quand un agent « prend en charge » un prospect (avant même le mandat), on crée
# automatiquement un dossier de commercialisation lié au lead, pour qu'il dispose
# tout de suite d'un espace clair où importer les pièces (identité, diagnostics,
# titre de propriété…). Tant qu'aucun mandat n'existe, le dossier porte une clé
# mandat factice `lead:<id>` ; à la création du mandat vente/location, on le
# raccroche au vrai mandat (link_lead_dossier_to_mandate) pour garder l'historique.


def lead_dossier_mandate_key(lead_id: int) -> str:
    return f"lead:{int(lead_id)}"


def dossier_from_lead(lead: dict) -> dict:
    """Préremplit un dossier depuis une fiche prospect (annonce)."""
    surface = lead.get("surface")
    price = lead.get("price")
    title = (
        lead.get("property_title")
        or lead.get("listing_title")
        or lead.get("address")
        or "Annonce prise en charge"
    )
    return {
        "title": str(title)[:120],
        "description": "",
        "property_address": (lead.get("address") or "").strip() if lead.get("address") not in (None, "—") else "",
        "postal_code": (lead.get("postcode") or "").strip(),
        "city": (lead.get("city") or "").strip(),
        "surface": float(surface) if surface not in (None, "", 0) else None,
        "rooms": "",
        "price": int(float(price)) if price not in (None, "", 0) else None,
        "property_type": (lead.get("property_type") or "").strip(),
    }


def get_lead_dossier(agency_id: str, lead_id: int) -> dict | None:
    """Dossier rattaché à un prospect (par lead_id ou clé mandat factice)."""
    key = lead_dossier_mandate_key(lead_id)
    with get_connection() as conn:
        ensure_dossier_tables(conn)
        row = conn.execute(
            """SELECT * FROM mandate_dossiers
               WHERE agency_id = ? AND (lead_id = ? OR mandate_id = ?)
               ORDER BY created_at ASC LIMIT 1""",
            (agency_id, int(lead_id), key),
        ).fetchone()
    return _row_dossier(row) if row else None


def ensure_lead_dossier(agency_id: str, lead_id: int, lead: dict | None = None) -> dict | None:
    """Renvoie le dossier du prospect, en le créant s'il n'existe pas encore (idempotent)."""
    if not agency_id or not lead_id:
        return None
    existing = get_lead_dossier(agency_id, lead_id)
    if existing:
        return existing
    data = dossier_from_lead(lead or {})
    data["lead_id"] = int(lead_id)
    data["status"] = "actif"
    return create_mandate_dossier(agency_id, lead_dossier_mandate_key(lead_id), data)


def link_lead_dossier_to_mandate(agency_id: str, lead_id: int, mandate_id: str) -> dict | None:
    """Raccroche le dossier auto du prospect au vrai mandat une fois celui-ci créé."""
    if not agency_id or not lead_id or not mandate_id:
        return None
    dossier = get_lead_dossier(agency_id, lead_id)
    if not dossier:
        return None
    # Ne réécrit que si le dossier est encore sur la clé factice (pas déjà lié).
    if dossier.get("mandate_id") not in (None, "", lead_dossier_mandate_key(lead_id)):
        return dossier
    now = _now()
    with get_connection() as conn:
        ensure_dossier_tables(conn)
        conn.execute(
            """UPDATE mandate_dossiers SET mandate_id = ?, updated_at = ?
               WHERE id = ? AND agency_id = ?""",
            (mandate_id, now, dossier["id"], agency_id),
        )
        conn.commit()
    return get_mandate_dossier(dossier["id"], agency_id)


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


# ── Espace documents type Drive (import, dossiers, pièces auto) ──────────────


def _save_documents(dossier_id: str, agency_id: str, folders: list[dict]) -> None:
    now = _now()
    payload = json.dumps({"folders": folders}, ensure_ascii=False)
    with get_connection() as conn:
        conn.execute(
            """UPDATE mandate_dossiers SET documents_json = ?, updated_at = ?
               WHERE id = ? AND agency_id = ?""",
            (payload, now, dossier_id, agency_id),
        )
        conn.commit()


def document_public_url(agency_id: str, dossier_id: str, filename: str) -> str:
    return f"/api/mandates/dossier-docs/{agency_id}/{dossier_id}/{filename}"


def get_dossier_documents(
    dossier_id: str,
    agency_id: str,
    mandate: dict | None = None,
) -> dict | None:
    """Vue fusionnée : checklist auto (selon profil) + dossiers/pièces importés."""
    row = get_mandate_dossier(dossier_id, agency_id)
    if not row:
        return None
    stored = (row.get("documents") or {}).get("folders") or []
    by_key = {f.get("key"): f for f in stored if f.get("key")}
    checklist = build_document_checklist(mandate)
    checklist_keys = {item["key"] for item in checklist}

    folders: list[dict] = []
    for item in checklist:
        f = by_key.get(item["key"]) or {}
        files = f.get("files") or []
        folders.append({
            "id": f.get("id") or item["key"],
            "key": item["key"],
            "name": item["name"],
            "description": item["description"],
            "required": item["required"],
            "auto": True,
            "custom": False,
            "files": files,
            "count": len(files),
            "complete": bool(files),
        })

    # Dossiers personnalisés créés par l'agent (hors checklist).
    for f in stored:
        if f.get("key") in checklist_keys:
            continue
        files = f.get("files") or []
        folders.append({
            "id": f.get("id") or f.get("key"),
            "key": f.get("key"),
            "name": f.get("name") or "Dossier",
            "description": f.get("description") or "",
            "required": False,
            "auto": False,
            "custom": True,
            "files": files,
            "count": len(files),
            "complete": bool(files),
        })

    required_total = sum(1 for it in checklist if it["required"])
    required_done = sum(1 for fo in folders if fo["required"] and fo["complete"])
    return {
        "dossier_id": dossier_id,
        "folders": folders,
        "profile": detect_seller_profile(mandate),
        "mandate_mentions": MANDATE_MENTIONS,
        "required_total": required_total,
        "required_done": required_done,
    }


def add_dossier_document(
    dossier_id: str,
    agency_id: str,
    folder_key: str,
    filename: str,
    raw: bytes,
    *,
    folder_name: str = "",
) -> dict | None:
    row = get_mandate_dossier(dossier_id, agency_id)
    if not row:
        return None
    folder_key = (folder_key or "").strip()
    if not folder_key:
        raise ValueError("Dossier de destination requis")
    ext = Path(filename).suffix.lower()
    if ext not in ALLOWED_DOC_EXT:
        raise ValueError("Format non pris en charge (PDF, image, Word, Excel…)")
    if len(raw) > MAX_DOC_BYTES:
        raise ValueError("Fichier trop lourd (max 25 Mo)")

    fid = str(uuid.uuid4())
    safe_name = f"{fid}{ext}"
    folder_dir = _dossier_dir(agency_id, dossier_id)
    folder_dir.mkdir(parents=True, exist_ok=True)
    (folder_dir / safe_name).write_bytes(raw)

    file_entry = {
        "id": fid,
        "filename": safe_name,
        "original_name": Path(filename).name,
        "url": document_public_url(agency_id, dossier_id, safe_name),
        "size": len(raw),
        "ext": ext.lstrip("."),
        "created_at": _now(),
    }

    folders = list((row.get("documents") or {}).get("folders") or [])
    target = next(
        (f for f in folders if f.get("key") == folder_key or f.get("id") == folder_key),
        None,
    )
    if target is None:
        target = {
            "id": folder_key,
            "key": folder_key,
            "name": (folder_name or folder_key).strip(),
            "files": [],
        }
        folders.append(target)
    target.setdefault("files", []).append(file_entry)
    _save_documents(dossier_id, agency_id, folders)
    return get_dossier_documents(dossier_id, agency_id)


def remove_dossier_document(
    dossier_id: str,
    agency_id: str,
    folder_key: str,
    file_id: str,
) -> dict | None:
    row = get_mandate_dossier(dossier_id, agency_id)
    if not row:
        return None
    folders = list((row.get("documents") or {}).get("folders") or [])
    removed = None
    for f in folders:
        if f.get("key") != folder_key and f.get("id") != folder_key:
            continue
        kept = []
        for item in f.get("files") or []:
            if item.get("id") == file_id:
                removed = item
            else:
                kept.append(item)
        f["files"] = kept
    if removed:
        path = _dossier_dir(agency_id, dossier_id) / removed.get("filename", "")
        if path.is_file():
            path.unlink(missing_ok=True)
        _save_documents(dossier_id, agency_id, folders)
    return get_dossier_documents(dossier_id, agency_id)


def create_dossier_folder(dossier_id: str, agency_id: str, name: str) -> dict | None:
    row = get_mandate_dossier(dossier_id, agency_id)
    if not row:
        return None
    name = (name or "").strip()
    if not name:
        raise ValueError("Nom du dossier requis")
    folders = list((row.get("documents") or {}).get("folders") or [])
    new_key = f"custom:{uuid.uuid4()}"
    folders.append({"id": new_key, "key": new_key, "name": name, "files": []})
    _save_documents(dossier_id, agency_id, folders)
    return get_dossier_documents(dossier_id, agency_id)


def delete_dossier_folder(dossier_id: str, agency_id: str, folder_key: str) -> dict | None:
    """Supprime un dossier personnalisé (les pièces auto de la checklist restent)."""
    row = get_mandate_dossier(dossier_id, agency_id)
    if not row:
        return None
    folders = list((row.get("documents") or {}).get("folders") or [])
    kept = []
    removed = None
    for f in folders:
        if f.get("key") == folder_key or f.get("id") == folder_key:
            removed = f
        else:
            kept.append(f)
    if removed:
        for item in removed.get("files") or []:
            path = _dossier_dir(agency_id, dossier_id) / item.get("filename", "")
            if path.is_file():
                path.unlink(missing_ok=True)
        _save_documents(dossier_id, agency_id, kept)
    return get_dossier_documents(dossier_id, agency_id)


def resolve_dossier_document_path(
    agency_id: str,
    dossier_id: str,
    filename: str,
) -> Path | None:
    if ".." in filename or "/" in filename or "\\" in filename:
        return None
    row = get_mandate_dossier(dossier_id, agency_id)
    if not row:
        return None
    allowed = set()
    for f in (row.get("documents") or {}).get("folders") or []:
        for item in f.get("files") or []:
            allowed.add(item.get("filename"))
    if filename not in allowed:
        return None
    path = _dossier_dir(agency_id, dossier_id) / filename
    return path if path.is_file() else None


def document_original_name(agency_id: str, dossier_id: str, filename: str) -> str:
    row = get_mandate_dossier(dossier_id, agency_id)
    if not row:
        return filename
    for f in (row.get("documents") or {}).get("folders") or []:
        for item in f.get("files") or []:
            if item.get("filename") == filename:
                return item.get("original_name") or filename
    return filename
