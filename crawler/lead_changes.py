"""Journal des ajouts / mises à jour prospect pendant la veille."""

from __future__ import annotations

from typing import Any


def _fmt_price(val: Any) -> str:
    try:
        n = int(val)
        return f"{n:,}".replace(",", " ")
    except (TypeError, ValueError):
        return str(val) if val else "—"


def diff_lead_fields(existing: dict | None, lead) -> list[str]:
    """Liste lisible des champs modifiés (français métier)."""
    if not existing:
        parts = []
        if getattr(lead, "price", None):
            parts.append(f"Prix {_fmt_price(lead.price)} €")
        if getattr(lead, "surface", None):
            parts.append(f"{lead.surface} m²")
        if getattr(lead, "address", None):
            parts.append(str(lead.address)[:80])
        phone = getattr(lead, "phone", None)
        if phone:
            parts.append(f"Tél. {phone}")
        return parts or ["Nouvelle fiche détectée"]

    changes: list[str] = []
    old_p, new_p = existing.get("price"), getattr(lead, "price", None)
    if new_p and old_p != new_p:
        changes.append(f"Prix {_fmt_price(old_p)} → {_fmt_price(new_p)} €")

    old_prev = existing.get("previous_price")
    if old_prev and new_p and int(old_prev) != int(new_p):
        changes.append("Baisse de prix signalée")

    old_s, new_s = existing.get("surface"), getattr(lead, "surface", None)
    if new_s and old_s != new_s:
        changes.append(f"Surface {old_s or '—'} → {new_s} m²")

    old_phone, new_phone = existing.get("phone"), getattr(lead, "phone", None)
    if new_phone and (old_phone or "") != (new_phone or ""):
        changes.append(f"Téléphone {'ajouté' if not old_phone else 'mis à jour'}")

    old_email, new_email = existing.get("email"), getattr(lead, "email", None)
    if new_email and (old_email or "") != (new_email or ""):
        changes.append(f"Email {'ajouté' if not old_email else 'mis à jour'}")

    old_addr, new_addr = existing.get("address"), getattr(lead, "address", None)
    if new_addr and (old_addr or "").strip() != (new_addr or "").strip():
        changes.append("Adresse mise à jour")

    old_type = existing.get("listing_type") or existing.get("type")
    new_type = getattr(lead, "listing_type", None)
    if new_type and old_type != new_type:
        changes.append(f"Type {old_type or '—'} → {new_type}")

    if not changes:
        changes.append("Fiche revue (données confirmées)")
    return changes


def record_lead_change(
    *,
    job_id: str | None,
    agency_id: str,
    lead_id: int,
    change_type: str,
    summary: str,
    details: list[str] | None = None,
    source_name: str | None = None,
    listing_url: str | None = None,
    owner_label: str | None = None,
) -> None:
    if not job_id or not agency_id or not lead_id:
        return
    from crawler.storage import insert_crawl_lead_change

    insert_crawl_lead_change(
        job_id=job_id,
        agency_id=agency_id,
        lead_id=lead_id,
        change_type=change_type,
        summary=summary[:500],
        details=details or [],
        source_name=source_name,
        listing_url=listing_url,
        owner_label=owner_label,
    )
