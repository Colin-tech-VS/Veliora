"""Demandes visiteurs sur annonces en ligne — activité CRM + notes prospect."""

from __future__ import annotations

import re
from datetime import datetime, timezone

from crawler.extractors import normalize_phone
from crawler.storage import add_activity, get_connection
from crm.portal.storage import (
    create_listing_inquiry,
    get_listing,
    list_listing_inquiries,
)

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
INQUIRY_KINDS = ("contact_agency", "info_request")


def _validate_inquiry_payload(data: dict, *, kind: str) -> str | None:
    name = (data.get("name") or data.get("contact_name") or "").strip()
    email = (data.get("email") or data.get("contact_email") or "").strip().lower()
    phone = normalize_phone((data.get("phone") or data.get("contact_phone") or "").strip()) or (
        (data.get("phone") or data.get("contact_phone") or "").strip()
    )
    message = (data.get("message") or "").strip()
    if len(name) < 2:
        return "Indiquez votre nom (2 caractères minimum)."
    if not email and not phone:
        return "Indiquez un email ou un téléphone pour que l'agence vous recontacte."
    if email and not _EMAIL_RE.match(email):
        return "Email invalide."
    if kind == "info_request" and len(message) < 10:
        return "Précisez votre demande (10 caractères minimum)."
    return None


def _append_lead_portal_note(lead_id: int, agency_id: str, line: str) -> None:
    from crawler.storage import get_lead

    lead = get_lead(lead_id, agency_id)
    if not lead:
        return
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M")
    note_line = f"[{stamp}] {line}"
    prev = (lead.get("notes") or "").strip()
    notes = f"{prev}\n{note_line}".strip() if prev else note_line
    with get_connection() as conn:
        conn.execute(
            "UPDATE leads SET notes = ?, updated_at = ? WHERE id = ? AND agency_id = ?",
            (notes, datetime.now(timezone.utc).isoformat(), lead_id, agency_id),
        )
        conn.commit()


def submit_listing_inquiry(listing_id: str, data: dict) -> dict:
    kind = (data.get("kind") or "contact_agency").strip().lower()
    if kind not in INQUIRY_KINDS:
        kind = "contact_agency"
    err = _validate_inquiry_payload(data, kind=kind)
    if err:
        return {"ok": False, "error": err}

    listing = get_listing(listing_id, public=True)
    if not listing:
        return {"ok": False, "error": "Annonce introuvable ou non publiée."}

    name = (data.get("name") or data.get("contact_name") or "").strip()
    email = (data.get("email") or data.get("contact_email") or "").strip().lower() or None
    phone = normalize_phone((data.get("phone") or data.get("contact_phone") or "").strip()) or (
        (data.get("phone") or data.get("contact_phone") or "").strip() or None
    )
    message = (data.get("message") or "").strip() or None

    inquiry = create_listing_inquiry(
        listing_id=listing_id,
        agency_id=listing["agency_id"],
        kind=kind,
        name=name,
        email=email,
        phone=phone,
        message=message,
        source_lead_id=listing.get("source_lead_id"),
    )

    agency_id = listing.get("agency_id")
    title_short = (listing.get("title") or "Annonce")[:60]
    if kind == "contact_agency":
        activity = f"Portail — contact agence · {name} · {title_short}"
        note = f"Portail annonce — contact agence — {name}"
        if phone:
            note += f" · {phone}"
        if email:
            note += f" · {email}"
    else:
        activity = f"Portail — demande d'info · {name} · {title_short}"
        note = f"Portail annonce — demande d'information — {name}"
        if message:
            note += f" — {message[:200]}"

    if agency_id:
        add_activity("portal", activity, agency_id)

    lead_id = listing.get("source_lead_id")
    if lead_id and agency_id:
        try:
            _append_lead_portal_note(int(lead_id), agency_id, note)
        except (TypeError, ValueError):
            pass

    return {"ok": True, "inquiry_id": inquiry.get("id"), "message": "Votre demande a été transmise à l'agence."}


def agency_listing_inquiries(agency_id: str, listing_id: str, *, limit: int = 50) -> dict:
    item = get_listing(listing_id, agency_id=agency_id)
    if not item:
        return {"ok": False, "error": "Annonce introuvable."}
    items = list_listing_inquiries(listing_id, agency_id=agency_id, limit=limit)
    return {"ok": True, "inquiries": items, "unread_count": sum(1 for x in items if not x.get("read_at"))}
