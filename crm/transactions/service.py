"""Cycle de vie complet d'une affaire (transaction) — source de vérité unique.

Étapes (ordre = avancement), calquées sur le workflow agence :
    1  prospect          Détecté (vu sur un portail)
    2  pris_en_charge    Pris en charge par un agent
    3  contacte          Contacté / estimé (appel, RDV, convaincre)
    4  mandat_cree       Mandat créé (vente ou location)
    5  mandat_valide     Mandat validé par les 2 parties (propriétaire + agent)
    6  publie            Annonce publiée sur le portail
    7  acquereur         Acquéreur / locataire rapproché
    8  visite            Visite / négociation
    9  dossier_acquereur Dossier acquéreur préparé
    10 compromis         Compromis / notaire
    11 vendu             Vendu — commission encaissée

L'étape est DÉRIVÉE d'artefacts réels (prise en charge, mandat, annonce, dossier,
jalons, commission) — jamais saisie à la main — pour que tous les onglets affichent
le même état. Règle métier centrale : **publier exige un mandat validé** (étape ≥ 5).
"""

from __future__ import annotations

from crawler.storage import get_connection, get_leads

STAGES: list[tuple[str, str, str]] = [
    ("prospect", "Nouveau", "Prendre en charge"),
    ("pris_en_charge", "En charge", "Appeler"),
    ("contacte", "Contacté", "Créer le mandat"),
    ("mandat_cree", "Mandat", "Valider"),
    ("mandat_valide", "Mandat OK", "Publier"),
    ("publie", "En ligne", "Rapprocher"),
    ("acquereur", "Client trouvé", "Visite"),
    ("visite", "Visite", "Dossier"),
    ("dossier_acquereur", "Dossier", "Compromis"),
    ("compromis", "Compromis", "Finaliser"),
    ("vendu", "Terminé", "Clôturé"),
]
_STAGE_INDEX = {key: i for i, (key, _l, _n) in enumerate(STAGES)}
_STAGE_LABEL = {key: label for key, label, _n in STAGES}
_STAGE_NEXT = {key: nxt for key, _l, nxt in STAGES}

PUBLISH_STAGE_KEY = "mandat_valide"


def stage_meta(stage: str) -> dict:
    return {
        "stage": stage,
        "stage_index": _STAGE_INDEX.get(stage, 0),
        "stage_total": len(STAGES),
        "stage_label": _STAGE_LABEL.get(stage, stage),
        "next_action": _STAGE_NEXT.get(stage, ""),
        "can_publish": _STAGE_INDEX.get(stage, 0) >= _STAGE_INDEX[PUBLISH_STAGE_KEY],
    }


# ── Maps (batch) ──────────────────────────────────────────────────────────

def _outcomes_by_lead(conn, agency_id: str) -> dict[int, set[str]]:
    out: dict[int, set[str]] = {}
    try:
        rows = conn.execute(
            "SELECT lead_id, outcome_type FROM lead_outcomes WHERE agency_id = ?",
            (agency_id,),
        ).fetchall()
    except Exception:
        return out
    for r in rows:
        out.setdefault(int(r["lead_id"]), set()).add((r["outcome_type"] or "").lower())
    return out


def _mandates_by_lead(agency_id: str) -> dict[int, list[dict]]:
    from crm.mandates.storage import list_seller_mandates

    by_lead: dict[int, list[dict]] = {}
    for m in list_seller_mandates(agency_id):
        if m.get("lead_id") is None:
            continue
        by_lead.setdefault(int(m["lead_id"]), []).append(m)
    return by_lead


def _listings_by_lead(agency_id: str) -> dict[int, list[dict]]:
    from crm.portal.storage import list_listings

    by_lead: dict[int, list[dict]] = {}
    for item in list_listings(agency_id=agency_id, limit=200):
        sl = item.get("source_lead_id")
        if sl is None:
            continue
        by_lead.setdefault(int(sl), []).append(item)
    return by_lead


def _best_mandate(mandates: list[dict]) -> dict | None:
    if not mandates:
        return None
    rank = {"signed": 4, "validated": 4, "sent": 2, "draft": 1}
    return sorted(mandates, key=lambda m: rank.get(m.get("status"), 0), reverse=True)[0]


def _mandate_validated(mandate: dict | None) -> bool:
    if not mandate:
        return False
    if mandate.get("status") in ("signed", "validated"):
        return True
    return bool(mandate.get("owner_validated_at") and mandate.get("agent_validated_at"))


def signed_mandate_for_lead(agency_id: str, lead_id: int) -> dict | None:
    """Mandat publiable pour ce lead = validé par les 2 parties (ou signé)."""
    mandates = _mandates_by_lead(agency_id).get(int(lead_id), [])
    for m in mandates:
        if _mandate_validated(m):
            return m
    return None


# ── Dérivation d'étape ──────────────────────────────────────────────────────

def derive_stage(
    *,
    assignment: dict | None,
    mandates: list[dict],
    listings: list[dict],
    outcomes: set[str],
    progress: dict | None = None,
    pipeline: str | None = None,
) -> str:
    progress = progress or {}
    if progress.get("sold_at"):
        return "vendu"
    if progress.get("compromis_at"):
        return "compromis"
    if progress.get("buyer_dossier_id"):
        return "dossier_acquereur"
    if progress.get("visit_at"):
        return "visite"
    if progress.get("buyer_client_id"):
        return "acquereur"

    if any((l.get("status") == "published") for l in listings):
        return "publie"

    best = _best_mandate(mandates)
    if best:
        if _mandate_validated(best):
            return "mandat_valide"
        return "mandat_cree"

    oc = outcomes or set()
    if oc & {"call", "rdv"} or (pipeline or "") in ("contacte", "contacté", "rdv", "mandat"):
        return "contacte"
    if assignment:
        return "pris_en_charge"
    return "prospect"


# ── Dossier dynamique (composé à la volée + overrides éditables) ────────────

def compose_dossier(agency_id: str, lead_id: int) -> dict:
    """Dossier complet et DYNAMIQUE d'une affaire.

    Agrège en direct : annonce (lead), vendeur (mandat/lead), acquéreur/locataire
    (property_client), agent (prise en charge), agence (profil légal), + mandat,
    annonce portail, photos et jalons. Les champs éditables persistés (table
    `mandate_dossiers`) sont fusionnés par-dessus → toujours modifiables.
    """
    from crawler.storage import get_agency_name, get_lead
    from crm.agents.storage import get_assignment
    from crm.mandates.dossiers import list_mandate_dossiers
    from crm.mandates.storage import get_agency_legal_profile, list_seller_mandates
    from crm.transactions.storage import get_progress

    lead = get_lead(int(lead_id), agency_id) or {}
    mandates = list_seller_mandates(agency_id, lead_id=int(lead_id))
    mandate = _best_mandate(mandates)
    assignment = get_assignment(agency_id, int(lead_id))
    progress = get_progress(agency_id, int(lead_id))
    agency_profile = get_agency_legal_profile(agency_id)

    # Overrides éditables (dossier lié au mandat) + photos / clients liés.
    stored = {}
    photos: list = []
    linked_clients: list = []
    dossier_id = None
    if mandate:
        existing = list_mandate_dossiers(mandate["id"], agency_id)
        if existing:
            d = existing[0]
            dossier_id = d["id"]
            photos = d.get("photos") or []
            linked_clients = d.get("linked_clients") or []
            stored = {
                k: v
                for k, v in d.items()
                if k in ("title", "description", "property_address", "postal_code",
                         "city", "surface", "rooms", "price", "property_type", "status")
                and v not in (None, "", [])
            }

    mf = (mandate or {}).get("fields") or {}

    # Acquéreur / locataire éventuel (depuis les jalons de progression).
    buyer = None
    buyer_id = progress.get("buyer_client_id")
    if buyer_id:
        from crm.mandates.storage import get_property_client

        buyer = get_property_client(buyer_id, agency_id)

    seller = {
        "first_name": mf.get("seller_first_name") or mf.get("owner_first_name") or lead.get("first_name"),
        "last_name": mf.get("seller_last_name") or mf.get("owner_last_name") or lead.get("last_name"),
        "email": mf.get("seller_email") or mf.get("owner_email") or (lead.get("email") if lead.get("email") != "—" else ""),
        "phone": mf.get("seller_phone") or mf.get("owner_phone") or (lead.get("phone") if lead.get("phone") != "—" else ""),
    }

    property_block = {
        "title": stored.get("title") or lead.get("property_title") or mf.get("property_address") or "Bien",
        "type": stored.get("property_type") or lead.get("property_type") or mf.get("property_type") or "",
        "address": stored.get("property_address") or lead.get("address") or mf.get("property_address") or "",
        "postcode": stored.get("postal_code") or lead.get("postcode") or mf.get("postal_code") or "",
        "city": stored.get("city") or lead.get("city") or mf.get("city") or "",
        "surface": stored.get("surface") if stored.get("surface") is not None else lead.get("surface"),
        "rooms": stored.get("rooms") or mf.get("rooms") or "",
        "price": stored.get("price") if stored.get("price") is not None else (lead.get("price") or mf.get("price_fai")),
        "transaction_type": lead.get("transaction_type") or (mandate or {}).get("mandate_type") or "vente",
        "image_url": lead.get("listing_image_url"),
    }

    agent_block = {
        "agent_id": (assignment or {}).get("agent_id") or (mandate or {}).get("agent_id"),
        "agent_name": (assignment or {}).get("agent_name") or (mandate or {}).get("agent_name"),
    }
    agency_block = {
        "name": agency_profile.get("brand_name") or agency_profile.get("legal_name") or get_agency_name(agency_id),
        "legal_name": agency_profile.get("legal_name"),
        "email": agency_profile.get("email"),
        "phone": agency_profile.get("phone"),
        "city": agency_profile.get("city"),
    }

    tx = transaction_for_lead(lead, agency_id) if lead else stage_meta("prospect")

    return {
        "ok": True,
        "lead_id": int(lead_id),
        "dossier_id": dossier_id,
        "editable": True,
        "transaction": tx,
        "property": property_block,
        "seller": seller,
        "buyer": buyer,
        "agent": agent_block,
        "agency": agency_block,
        "mandate": {
            "id": (mandate or {}).get("id"),
            "type": (mandate or {}).get("mandate_type"),
            "status": (mandate or {}).get("status"),
            "exclusivity": (mandate or {}).get("exclusivity"),
            "owner_validated_at": (mandate or {}).get("owner_validated_at"),
            "agent_validated_at": (mandate or {}).get("agent_validated_at"),
            "validated": _mandate_validated(mandate),
        },
        "milestones": {
            "visit_at": progress.get("visit_at"),
            "compromis_at": progress.get("compromis_at"),
            "sold_at": progress.get("sold_at"),
        },
        "photos": photos,
        "linked_clients": linked_clients,
        "description": stored.get("description") or "",
    }


# ── Vue par lead + tableau de bord ──────────────────────────────────────────

def transaction_for_lead(lead: dict, agency_id: str) -> dict:
    from crm.agents.storage import get_assignment
    from crm.transactions.storage import get_progress

    lead_id = int(lead["id"])
    assignment = get_assignment(agency_id, lead_id)
    mandates = _mandates_by_lead(agency_id).get(lead_id, [])
    listings = _listings_by_lead(agency_id).get(lead_id, [])
    progress = get_progress(agency_id, lead_id)
    with get_connection() as conn:
        outcomes = _outcomes_by_lead(conn, agency_id).get(lead_id, set())
    stage = derive_stage(
        assignment=assignment,
        mandates=mandates,
        listings=listings,
        outcomes=outcomes,
        progress=progress,
        pipeline=lead.get("pipeline"),
    )
    best = _best_mandate(mandates)
    published = next((l for l in listings if l.get("status") == "published"), None)
    return {
        **stage_meta(stage),
        "lead_id": lead_id,
        "agent_id": (assignment or {}).get("agent_id"),
        "agent_name": (assignment or {}).get("agent_name"),
        "mandate_id": (best or {}).get("id"),
        "mandate_status": (best or {}).get("status"),
        "mandate_validated": _mandate_validated(best),
        "listing_id": (published or {}).get("id"),
    }


def attach_transactions(leads: list[dict], agency_id: str) -> list[dict]:
    """Annoter chaque lead avec son étape transaction (cohérence inter-onglets).

    Batch : maps construites une fois. N'appelle PAS get_leads (anti-récursion).
    """
    if not leads or not agency_id:
        return leads
    from crm.agents.storage import get_assignments_map
    from crm.transactions.storage import get_progress_map

    try:
        assignments = get_assignments_map(agency_id)
        mandates_by_lead = _mandates_by_lead(agency_id)
        listings_by_lead = _listings_by_lead(agency_id)
        progress_map = get_progress_map(agency_id)
        with get_connection() as conn:
            outcomes_by_lead = _outcomes_by_lead(conn, agency_id)
    except Exception:
        return leads

    for lead in leads:
        try:
            lead_id = int(lead["id"])
        except (KeyError, TypeError, ValueError):
            continue
        assignment = assignments.get(lead_id)
        mandates = mandates_by_lead.get(lead_id, [])
        stage = derive_stage(
            assignment=assignment,
            mandates=mandates,
            listings=listings_by_lead.get(lead_id, []),
            outcomes=outcomes_by_lead.get(lead_id, set()),
            progress=progress_map.get(lead_id, {}),
            pipeline=lead.get("pipeline"),
        )
        lead["transaction"] = {
            **stage_meta(stage),
            "agent_id": (assignment or {}).get("agent_id"),
            "agent_name": (assignment or {}).get("agent_name"),
            "mandate_id": (_best_mandate(mandates) or {}).get("id"),
            "mandate_status": (_best_mandate(mandates) or {}).get("status"),
        }
    return leads


def build_transactions(agency_id: str, *, for_agent_id: str | None = None) -> dict:
    """Tableau de bord des affaires (onglet Transactions).

    `for_agent_id` : ne renvoie que les affaires prises en charge par cet agent
    (utilisé par le Pipeline, qui n'affiche que les annonces de l'agent connecté).
    """
    from crm.agents.storage import get_assignments_map, list_agents
    from crm.transactions.storage import get_progress_map

    leads = {int(l["id"]): l for l in get_leads(agency_id)}
    assignments = get_assignments_map(agency_id)
    mandates_by_lead = _mandates_by_lead(agency_id)
    listings_by_lead = _listings_by_lead(agency_id)
    progress_map = get_progress_map(agency_id)
    with get_connection() as conn:
        outcomes_by_lead = _outcomes_by_lead(conn, agency_id)

    active_ids = (
        set(assignments)
        | set(mandates_by_lead)
        | set(listings_by_lead)
        | set(progress_map)
        | set(outcomes_by_lead)
    )

    deals: list[dict] = []
    for lead_id in active_ids:
        lead = leads.get(lead_id)
        if not lead:
            continue
        assignment = assignments.get(lead_id)
        if for_agent_id and (assignment or {}).get("agent_id") != for_agent_id:
            continue
        mandates = mandates_by_lead.get(lead_id, [])
        listings = listings_by_lead.get(lead_id, [])
        progress = progress_map.get(lead_id, {})
        stage = derive_stage(
            assignment=assignment,
            mandates=mandates,
            listings=listings,
            outcomes=outcomes_by_lead.get(lead_id, set()),
            progress=progress,
            pipeline=lead.get("pipeline"),
        )
        best = _best_mandate(mandates)
        published = next((l for l in listings if l.get("status") == "published"), None)
        deals.append(
            {
                **stage_meta(stage),
                "lead_id": lead_id,
                "owner": lead.get("owner"),
                "property_title": lead.get("property_title"),
                "property_type": lead.get("property_type"),
                "city": lead.get("city"),
                "address": lead.get("address"),
                "price": lead.get("price"),
                "surface": lead.get("surface"),
                "transaction_type": lead.get("transaction_type"),
                "mandate_score": lead.get("mandate_score"),
                "agent_id": (assignment or {}).get("agent_id"),
                "agent_name": (assignment or {}).get("agent_name"),
                "mandate_id": (best or {}).get("id"),
                "mandate_status": (best or {}).get("status"),
                "mandate_type": (best or {}).get("mandate_type"),
                "mandate_validated": _mandate_validated(best),
                "listing_id": (published or {}).get("id"),
            }
        )

    deals.sort(key=lambda d: (d["stage_index"], -(d.get("mandate_score") or 0)), reverse=True)

    counts: dict[str, int] = {key: 0 for key, _l, _n in STAGES}
    for d in deals:
        counts[d["stage"]] = counts.get(d["stage"], 0) + 1

    return {
        "ok": True,
        "stages": [
            {"key": key, "label": label, "count": counts.get(key, 0)}
            for key, label, _n in STAGES
        ],
        "deals": deals,
        "agents": list_agents(agency_id),
        "publish_requires_validated_mandate": True,
    }
