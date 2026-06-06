"""Création prospect + estimation vitrine (pool partagé, contact agence en 2e étape)."""

from __future__ import annotations

import json
import re
import uuid
from typing import Any

from crawler.extractors import LeadData, normalize_phone
from crawler.storage import _now, add_activity, get_connection, list_agency_ids, save_lead
from crawler.validation import _email_ok, _phone_ok
from crm.estimator.service import build_price_estimate, estimator_form_schema
from crm.estimator.storage import save_lead_estimate
from crm.leads.shared_pool import lead_visible_to_agency, pool_agency_id

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

_FEATURE_KEYS = (
    "has_elevator",
    "has_parking",
    "has_outdoor",
    "has_cellar",
    "has_view",
    "bright",
    "recent_renovation",
    "noise_nuisance",
    "prime_sector",
)


def vitrine_estimator_config() -> dict:
    return {
        "estimator_enabled": True,
        "shared_prospects_pool": True,
    }


def _parse_surface(data: dict) -> float | None:
    try:
        s = float(data.get("surface") if data.get("surface") not in (None, "") else 0)
        return s if s > 0 else None
    except (TypeError, ValueError):
        return None


def _collect_inputs(data: dict, surface: float) -> dict[str, Any]:
    inputs = dict(data.get("inputs") or {})
    inputs.setdefault("surface", surface)
    prop = (data.get("property_type") or inputs.get("property_type") or "appartement").strip().lower()
    inputs["property_type"] = prop
    for key in (
        "rooms",
        "floor",
        "condition",
        "dpe",
        "exposure",
        "construction_period",
        "commission_pct",
        "address",
        "city",
        "postcode",
    ):
        if data.get(key) not in (None, "") and key not in inputs:
            inputs[key] = data.get(key)
    for key in _FEATURE_KEYS:
        if key in data:
            inputs[key] = bool(data.get(key))
        elif key in inputs:
            inputs[key] = bool(inputs.get(key))
    return inputs


def validate_owner_required(data: dict) -> str | None:
    first = (data.get("first_name") or "").strip()
    last = (data.get("last_name") or "").strip()
    phone = normalize_phone((data.get("phone") or "").strip()) or (data.get("phone") or "").strip()
    email = (data.get("email") or "").strip().lower()

    if len(first) < 2:
        return "Prénom du propriétaire requis (2 caractères minimum)."
    if len(last) < 2:
        return "Nom du propriétaire requis (2 caractères minimum)."
    if not _phone_ok(phone) and not _email_ok(email):
        return "Téléphone ou email du propriétaire requis."
    if email and not _EMAIL_RE.match(email):
        return "Adresse email invalide."
    return None


def validate_property_required(data: dict) -> tuple[float | None, str | None]:
    address = (data.get("address") or "").strip()
    city = (data.get("city") or "").strip()
    surface = _parse_surface(data)
    if not surface:
        return None, "Surface habitable (m²) requise."
    if not address and not city:
        return None, "Adresse ou ville du bien requise."
    if address and len(address) < 5:
        return None, "Adresse trop courte."
    return surface, None


def _vitrine_notes_meta(notes: str | None) -> dict:
    if not notes:
        return {}
    try:
        parsed = json.loads(notes)
        if isinstance(parsed, dict):
            inner = parsed.get("vitrine")
            if isinstance(inner, dict):
                return inner
    except (json.JSONDecodeError, TypeError):
        pass
    return {}


def _write_vitrine_notes(lead_id: int, meta: dict) -> None:
    payload = json.dumps({"vitrine": meta}, ensure_ascii=False)
    with get_connection() as conn:
        conn.execute(
            "UPDATE leads SET notes = ?, updated_at = ? WHERE id = ? "
            "AND (agency_id IS NULL OR TRIM(COALESCE(agency_id, '')) = '')",
            (payload, _now(), lead_id),
        )
        conn.commit()


def _get_pool_vitrine_lead(lead_id: int) -> dict | None:
    from crawler.storage import _row_to_lead

    with get_connection() as conn:
        row = conn.execute(
            "SELECT * FROM leads WHERE id = ? "
            "AND (agency_id IS NULL OR TRIM(COALESCE(agency_id, '')) = '')",
            (lead_id,),
        ).fetchone()
    if not row:
        return None
    lead = _row_to_lead(row, enrich_scores=False)
    meta = _vitrine_notes_meta(lead.get("notes"))
    if not meta.get("vitrine_estimate"):
        return None
    lead["vitrine_meta"] = meta
    return lead


def agencies_for_vitrine_lead(lead: dict) -> list[str]:
    from crm.billing.access import agency_has_active_subscription

    matched: list[str] = []
    # Widget marque blanche : l'agence émettrice est toujours destinataire (si abonnée).
    embed_aid = (lead.get("vitrine_meta") or {}).get("embed_agency_id")
    if embed_aid and agency_has_active_subscription(embed_aid):
        matched.append(embed_aid)
    for aid in list_agency_ids():
        if aid in matched:
            continue
        if not agency_has_active_subscription(aid):
            continue
        if lead_visible_to_agency(lead, aid):
            matched.append(aid)
    return matched


def notify_agencies_vitrine_sale_request(lead: dict) -> int:
    lead_id = lead.get("id")
    city = (lead.get("city") or "").strip()
    title = (lead.get("listing_title") or "Bien estimé")[:80]
    owner = f"{(lead.get('first_name') or '').strip()} {(lead.get('last_name') or '').strip()}".strip()
    if not owner or owner == "Prospect":
        owner = "Propriétaire (estimation vitrine)"
    text = f"Demande de vente — estimation vitrine · {owner} · {title}"
    if city:
        text += f" · {city}"
    count = 0
    for aid in agencies_for_vitrine_lead(lead):
        add_activity("lead", text, aid)
        count += 1
    if lead_id and count:
        _merge_vitrine_meta(
            int(lead_id),
            agencies_notified=count,
            notified_at=_now(),
        )
    return count


def _merge_vitrine_meta(lead_id: int, **kwargs) -> None:
    lead = _get_pool_vitrine_lead(lead_id)
    if not lead:
        return
    meta = dict(lead.get("vitrine_meta") or {})
    meta.update(kwargs)
    _write_vitrine_notes(lead_id, meta)


def create_prospect_from_estimate_form(
    data: dict,
    *,
    source_label: str = "Estimation",
    origin: str = "crm",
    require_owner: bool = False,
    require_consent: bool = False,
    discovering_agency_id: str | None = None,
    embed_agency_id: str | None = None,
) -> dict:
    has_owner_hint = any(
        (data.get(k) or "").strip()
        for k in ("first_name", "last_name", "phone", "email")
    )
    if require_owner or has_owner_hint:
        owner_err = validate_owner_required(data)
        if owner_err:
            return {"ok": False, "error": owner_err}

    surface, prop_err = validate_property_required(data)
    if prop_err:
        return {"ok": False, "error": prop_err}

    if require_consent and not data.get("consent"):
        return {
            "ok": False,
            "error": "Acceptez la politique de confidentialité pour envoyer votre demande.",
        }

    inputs = _collect_inputs(data, surface)
    address = (inputs.get("address") or data.get("address") or "").strip() or None
    city = (inputs.get("city") or data.get("city") or "").strip() or None
    postcode = (inputs.get("postcode") or data.get("postcode") or "").strip() or None

    prop_type = inputs.get("property_type") or "appartement"
    type_label = {
        "appartement": "Appartement",
        "maison": "Maison",
        "studio": "Studio",
        "terrain": "Terrain",
    }.get(str(prop_type).lower(), "Bien")
    title = (data.get("property_title") or "").strip() or (
        f"{type_label} {surface:g} m²" + (f" — {city}" if city else "")
    )

    first_name = (data.get("first_name") or "").strip()
    last_name = (data.get("last_name") or "").strip()
    phone = normalize_phone((data.get("phone") or "").strip()) or (data.get("phone") or "").strip() or None
    email = (data.get("email") or "").strip().lower() or None

    url_suffix = uuid.uuid4().hex
    contact_token = uuid.uuid4().hex
    if origin == "vitrine":
        source_url = f"https://veliora.fr/estimation/{url_suffix}"
    else:
        aid = discovering_agency_id or "crm"
        source_url = f"https://veliora.fr/estimation/{aid}/{url_suffix}"

    lead = LeadData(
        first_name=first_name,
        last_name=last_name,
        phone=phone,
        email=email,
        address=address,
        city=city,
        postcode=postcode,
        surface=surface,
        price=None,
        transaction_type="vente",
        source=source_label,
        source_url=source_url,
        type="particulier",
    )
    lead.raw_extras["listing_title"] = title
    lead.raw_extras["estimator_inputs"] = inputs
    lead.raw_extras["estimator_origin"] = origin
    if origin == "vitrine":
        lead.raw_extras["vitrine_estimate"] = True

    try:
        saved = save_lead(
            lead,
            agency_id=discovering_agency_id,
            require_verification=False,
        )
    except Exception as exc:
        return {"ok": False, "error": str(exc)[:200]}

    if not saved or not saved.get("id"):
        err = (saved or {}).get("errors") or ["Création du prospect impossible."]
        return {"ok": False, "error": err[0] if isinstance(err, list) else str(err)}

    if not saved.get("verified"):
        return {
            "ok": False,
            "error": "Fiche incomplète — vérifiez l'adresse, la surface et la localisation.",
        }

    lead_id = int(saved["id"])
    vitrine_meta = {
        "vitrine_estimate": True,
        "contact_token": contact_token,
        "wants_agency_contact": None,
        "estimator_origin": origin,
    }
    if embed_agency_id:
        # Widget marque blanche : l'agence émettrice reçoit toujours le lead.
        vitrine_meta["embed_agency_id"] = embed_agency_id
    _write_vitrine_notes(lead_id, vitrine_meta)

    from crawler.storage import _row_to_lead

    with get_connection() as conn:
        row = conn.execute("SELECT * FROM leads WHERE id = ?", (lead_id,)).fetchone()
    full_lead = _row_to_lead(row, enrich_scores=False) if row else None

    estimate = None
    try:
        estimate = build_price_estimate(full_lead or {"id": lead_id, **inputs}, inputs)
        if estimate.get("ok"):
            save_lead_estimate(lead_id, pool_agency_id(), estimate)
    except Exception:
        pass

    return {
        "ok": True,
        "lead": full_lead,
        "estimate": estimate,
        "created": bool(saved.get("created")),
        "lead_id": lead_id,
        "contact_token": contact_token,
    }


def update_vitrine_lead_contact(data: dict) -> dict:
    try:
        lead_id = int(data.get("lead_id") or 0)
    except (TypeError, ValueError):
        return {"ok": False, "error": "Identifiant de demande invalide."}

    token = (data.get("contact_token") or "").strip()
    wants = data.get("wants_agency_contact")
    if wants is None:
        wants = data.get("wants_contact")
    wants_contact = wants in (True, "true", "1", 1, "yes", "oui")

    lead = _get_pool_vitrine_lead(lead_id)
    if not lead:
        return {"ok": False, "error": "Demande introuvable ou expirée."}

    meta = lead.get("vitrine_meta") or {}
    if not token or token != meta.get("contact_token"):
        return {"ok": False, "error": "Session invalide — relancez une estimation."}

    if meta.get("wants_agency_contact") is not None:
        return {
            "ok": True,
            "already_answered": True,
            "wants_agency_contact": bool(meta.get("wants_agency_contact")),
            "agencies_notified": int(meta.get("agencies_notified") or 0),
        }

    first = (data.get("first_name") or "").strip()
    last = (data.get("last_name") or "").strip()
    phone = normalize_phone((data.get("phone") or "").strip()) or (data.get("phone") or "").strip()
    email = (data.get("email") or "").strip().lower()

    if wants_contact:
        if not data.get("consent"):
            return {
                "ok": False,
                "error": "Acceptez d’être contacté par une agence de votre secteur.",
            }
        owner_err = validate_owner_required(
            {"first_name": first, "last_name": last, "phone": phone, "email": email}
        )
        if owner_err:
            return {"ok": False, "error": owner_err}

    now = _now()
    meta["wants_agency_contact"] = wants_contact
    meta["contact_answered_at"] = now
    agencies_count = 0

    if wants_contact:
        with get_connection() as conn:
            conn.execute(
                """UPDATE leads SET
                   first_name = ?, last_name = ?, phone = ?, email = ?,
                   pipeline = 'nouveau', status = 'nouveau', updated_at = ?
                   WHERE id = ? AND (agency_id IS NULL OR TRIM(COALESCE(agency_id, '')) = '')""",
                (first, last, phone, email, now, lead_id),
            )
            conn.commit()

        from crawler.storage import _row_to_lead

        with get_connection() as conn:
            row = conn.execute("SELECT * FROM leads WHERE id = ?", (lead_id,)).fetchone()
        updated = _row_to_lead(row, enrich_scores=False) if row else lead
        agencies_count = notify_agencies_vitrine_sale_request(updated)
        meta["agencies_notified"] = agencies_count
    else:
        meta["prospect_only"] = True

    _write_vitrine_notes(lead_id, meta)

    return {
        "ok": True,
        "wants_agency_contact": wants_contact,
        "agencies_notified": agencies_count,
        "lead_id": lead_id,
    }


def public_estimate_response_payload(result: dict) -> dict:
    if not result.get("ok"):
        return {"ok": False, "error": result.get("error") or result.get("reason")}

    est = result.get("estimate") or {}
    if not est.get("ok"):
        return {
            "ok": False,
            "error": est.get("reason") or est.get("error") or "Estimation indisponible pour ce bien.",
            "saved": True,
            "lead_id": result.get("lead_id"),
            "contact_token": result.get("contact_token"),
        }

    estimate_out = {k: v for k, v in est.items() if k != "ok"}
    return {
        "ok": True,
        "saved": True,
        "lead_id": result.get("lead_id"),
        "contact_token": result.get("contact_token"),
        "estimate": estimate_out,
    }


def handle_public_vitrine_estimate(data: dict) -> dict:
    # Widget marque blanche : ?agency=<slug> attribue le lead à l'agence émettrice.
    embed_agency_id = None
    slug = (data.get("agency") or data.get("agency_slug") or "").strip()
    if slug:
        from crawler.storage import get_agency_id_by_slug

        embed_agency_id = get_agency_id_by_slug(slug)
    result = create_prospect_from_estimate_form(
        data,
        source_label="Estimation (site agence)" if embed_agency_id else "Estimation vitrine",
        origin="vitrine",
        require_owner=True,
        require_consent=True,
        embed_agency_id=embed_agency_id,
    )
    return public_estimate_response_payload(result)


def handle_public_vitrine_contact(data: dict) -> dict:
    return update_vitrine_lead_contact(data)
