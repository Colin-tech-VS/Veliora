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


def publish_listing_from_lead(
    agency_id: str,
    lead_id: int,
    data: dict | None = None,
) -> dict:
    """Publie une annonce détectée (lead crawlé) — exige un MANDAT SIGNÉ.

    Règle métier du workflow transaction : on ne met en ligne un bien que lorsque
    l'agent a signé le mandat. À défaut, on refuse avec un message explicite et le
    code `signed_mandate_required` pour que l'UI guide vers la signature.
    """
    from crawler.storage import get_lead
    from crm.agents.storage import assign_lead, get_assignment
    from crm.transactions.service import signed_mandate_for_lead

    data = data or {}
    lead = get_lead(int(lead_id), agency_id)
    if not lead:
        return {"ok": False, "error": "Prospect introuvable."}

    mandate = signed_mandate_for_lead(agency_id, int(lead_id))
    if not mandate:
        return {
            "ok": False,
            "error": "Mandat signé requis avant publication. Préparez puis signez le mandat.",
            "code": "signed_mandate_required",
        }

    # L'agent en charge = celui passé, sinon celui déjà assigné. On (re)pose la
    # prise en charge pour garantir le rattachement au portefeuille.
    agent_id = (data.get("agent_id") or "").strip() or (get_assignment(agency_id, int(lead_id)) or {}).get("agent_id")
    agent_name = None
    if agent_id:
        res = assign_lead(agency_id, int(lead_id), agent_id)
        if res.get("ok"):
            agent_name = res.get("agent_name")

    fields = mandate.get("fields") or {}
    title = (
        (data.get("title") or "").strip()
        or (lead.get("property_title") or "").strip()
        or f"{(lead.get('property_type') or 'Bien')} {lead.get('city') or ''}".strip()
    )
    listing_data = {
        "title": title,
        "description": (data.get("description") or "").strip(),
        "transaction_type": lead.get("transaction_type") or "vente",
        "property_type": (lead.get("property_type") or "appartement").lower(),
        "price": data.get("price") or lead.get("price"),
        "surface": data.get("surface") or lead.get("surface"),
        "rooms": data.get("rooms") or fields.get("rooms"),
        "city": lead.get("city") or fields.get("city") or "",
        "postcode": lead.get("postcode") or fields.get("postal_code"),
        "address": lead.get("address"),
        "image_url": (data.get("image_url") or lead.get("listing_image_url") or "").strip() or None,
        "status": (data.get("status") or "published").strip().lower(),
    }
    err = validate_listing_data(listing_data, require_contact=False)
    if err:
        return {"ok": False, "error": err}

    item = create_listing(
        {**listing_data, "agent_id": agent_id, "agent_name": agent_name, "source_lead_id": int(lead_id)},
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
