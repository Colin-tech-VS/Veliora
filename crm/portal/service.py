"""Validation et création annonces portail."""

from __future__ import annotations

import re

from crawler.extractors import normalize_phone
from crawler.validation import _email_ok, _phone_ok
from crm.portal.storage import create_listing, get_listing, public_listing_payload, update_listing

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def validate_listing_data(data: dict, *, require_contact: bool = True) -> str | None:
    title = (data.get("title") or "").strip()
    city = (data.get("city") or "").strip()
    if len(title) < 5:
        return "Titre requis (5 caractères minimum)."
    if not city:
        return "Ville requise."
    try:
        price = int(data.get("price") or 0)
        if price <= 0:
            return "Prix requis (entier > 0)."
    except (TypeError, ValueError):
        return "Prix invalide."
    try:
        surface = float(data.get("surface") or 0)
        if surface <= 0:
            return "Surface requise (m² > 0)."
    except (TypeError, ValueError):
        return "Surface invalide."
    if require_contact:
        phone = normalize_phone((data.get("contact_phone") or "").strip()) or (data.get("contact_phone") or "").strip()
        email = (data.get("contact_email") or "").strip().lower()
        name = (data.get("contact_name") or "").strip()
        if not name:
            return "Nom du contact requis."
        if not _phone_ok(phone) and not _email_ok(email):
            return "Téléphone ou email de contact requis."
        if email and not _EMAIL_RE.match(email):
            return "Email invalide."
    return None


def create_public_listing(data: dict) -> dict:
    """Désactivé — seules les agences publient via le CRM."""
    return {
        "ok": False,
        "error": "La publication est réservée aux agences immobilières (espace CRM).",
    }


def create_agency_listing(agency_id: str, data: dict) -> dict:
    err = validate_listing_data(data, require_contact=False)
    if err:
        return {"ok": False, "error": err}
    status = (data.get("status") or "published").strip().lower()
    if status not in ("draft", "published", "archived", "pending"):
        status = "published"
    item = create_listing(
        {**data, "status": status},
        agency_id=agency_id,
        publisher_type="agency",
    )
    return {"ok": True, "listing": item}


def update_agency_listing(agency_id: str, listing_id: str, data: dict) -> dict:
    item = get_listing(listing_id, agency_id=agency_id)
    if not item:
        return {"ok": False, "error": "Annonce introuvable."}
    merged = {**item, **data}
    err = validate_listing_data(merged, require_contact=False)
    if err:
        return {"ok": False, "error": err}
    updated = update_listing(listing_id, agency_id, data)
    if not updated:
        return {"ok": False, "error": "Mise à jour impossible."}
    return {"ok": True, "listing": updated}
