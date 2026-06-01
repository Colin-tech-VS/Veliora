"""Rapproche une annonce des profils acheteurs / locataires de l'agence.

Objectif : pour chaque bien, lister les clients compatibles et déterminer
LA transaction la plus pertinente (vente via acheteurs / location via locataires)
selon la demande réellement enregistrée dans la base.
"""

from __future__ import annotations

import re
import unicodedata

# acheteur -> achète (vente) ; locataire -> loue (location)
_SEGMENT_TX = {"acheteur": "vente", "locataire": "location"}

_TYPE_ALIASES = {
    "studio": "appartement",
    "appartement": "appartement",
    "maison": "maison",
    "villa": "maison",
    "terrain": "terrain",
}


def _norm(s: str | None) -> str:
    s = (s or "").strip().lower()
    s = "".join(c for c in unicodedata.normalize("NFD", s) if unicodedata.category(c) != "Mn")
    return re.sub(r"\s+", " ", s)


def _guess_type(lead: dict) -> str:
    title = _norm(lead.get("property_title") or lead.get("listing_title") or lead.get("address"))
    if "studio" in title:
        return "appartement"
    if any(w in title for w in ("maison", "villa", "pavillon", "longere")):
        return "maison"
    if "terrain" in title:
        return "terrain"
    return "appartement"


def _lead_rooms(lead: dict) -> int | None:
    for key in ("rooms", "pieces", "nb_pieces"):
        v = lead.get(key)
        if v:
            try:
                return int(float(str(v).replace(",", ".")))
            except (TypeError, ValueError):
                pass
    title = _norm(lead.get("property_title") or lead.get("listing_title"))
    m = re.search(r"\b[tf]\s?(\d)\b", title) or re.search(r"(\d+)\s*pi[eè]ce", title)
    if m:
        try:
            return int(m.group(1))
        except ValueError:
            return None
    return None


def _type_compatible(lead_type: str, client_type: str | None) -> bool:
    if not client_type:
        return True
    return _TYPE_ALIASES.get(_norm(client_type), _norm(client_type)) == _TYPE_ALIASES.get(
        lead_type, lead_type
    )


def _city_compatible(lead: dict, client: dict) -> tuple[bool, bool]:
    """(compatible, contrainte_appliquée). Sans villes client -> neutre (compatible)."""
    cities = [c for c in (client.get("cities") or []) if c]
    if not cities:
        return True, False
    lead_city = _norm(lead.get("city"))
    lead_pc = _norm(lead.get("postcode"))
    if not lead_city and not lead_pc:
        return True, False
    norm_cities = {_norm(c) for c in cities}
    hit = lead_city in norm_cities or any(lead_city and lead_city in c for c in norm_cities)
    return hit, True


def score_client_for_lead(lead: dict, client: dict) -> dict | None:
    """Note de compatibilité 0..100 + raisons. None si incompatible (ville/type)."""
    segment = (client.get("segment") or "acheteur").lower()
    expected_tx = _SEGMENT_TX.get(segment, "vente")
    lead_tx = (lead.get("transaction_type") or "vente").lower()

    lead_type = _guess_type(lead)
    if not _type_compatible(lead_type, client.get("property_type")):
        return None

    city_ok, city_constrained = _city_compatible(lead, client)
    if not city_ok:
        return None

    reasons: list[str] = []
    score = 40.0  # base : type + ville OK
    if city_constrained:
        score += 15
        reasons.append("Secteur recherché")

    # Surface
    surface = lead.get("surface")
    smin = client.get("surface_min")
    if smin and surface:
        if surface + 0.5 >= smin:
            score += 12
            reasons.append(f"Surface ≥ {int(smin)} m²")
        else:
            return None  # surface insuffisante = élimination

    # Pièces
    rooms = _lead_rooms(lead)
    rmin = client.get("rooms_min")
    if rmin and rooms is not None:
        if rooms >= rmin:
            score += 10
            reasons.append(f"{rooms} pièces ≥ {rmin}")
        else:
            return None

    # Budget — comparable seulement si l'unité correspond (prix vente vs loyer)
    price = int(lead.get("price") or 0)
    bmin = client.get("budget_min")
    bmax = client.get("budget_max")
    price_comparable = lead_tx == expected_tx and price > 0
    in_budget = None
    if price_comparable and (bmin or bmax):
        lo = bmin or 0
        hi = bmax or 0
        if hi and price > hi * 1.05:
            in_budget = False
            reasons.append("Au-dessus du budget")
            score -= 25
        elif lo and price < lo * 0.75:
            in_budget = False
            reasons.append("Sous la cible budget")
            score -= 8
        else:
            in_budget = True
            score += 23
            reasons.append("Dans le budget")
    elif not price_comparable and (bmin or bmax):
        reasons.append("Prix non comparable (transaction différente)")

    score = max(0.0, min(100.0, score))
    return {
        "client_id": client.get("id"),
        "name": client.get("full_name") or "Client",
        "segment": segment,
        "expected_transaction": expected_tx,
        "phone": client.get("phone"),
        "email": client.get("email"),
        "budget_min": bmin,
        "budget_max": bmax,
        "score": round(score),
        "in_budget": in_budget,
        "price_comparable": price_comparable,
        "reasons": reasons[:4],
    }


def build_lead_matches(lead: dict, clients: list[dict], *, top_n: int = 8) -> dict:
    """Rapproche un bien de tous les clients ; recommande la transaction la plus porteuse."""
    matches: list[dict] = []
    for client in clients:
        if (client.get("status") or "actif") not in ("actif", "", None):
            continue
        scored = score_client_for_lead(lead, client)
        if scored and scored["score"] >= 45:
            matches.append(scored)

    matches.sort(key=lambda m: m["score"], reverse=True)

    vente = [m for m in matches if m["expected_transaction"] == "vente"]
    location = [m for m in matches if m["expected_transaction"] == "location"]

    def _strength(rows: list[dict]) -> float:
        # demande = nb de bons matches pondéré par le meilleur score
        if not rows:
            return 0.0
        strong = sum(1 for r in rows if r["score"] >= 65)
        return strong * 100 + rows[0]["score"] + len(rows)

    lead_tx = (lead.get("transaction_type") or "vente").lower()
    sv, sl = _strength(vente), _strength(location)
    if sv == 0 and sl == 0:
        recommended = lead_tx
        reason = "Aucun acquéreur/locataire compatible enregistré pour l'instant."
    elif sv >= sl:
        recommended = "vente"
        reason = f"{len(vente)} acquéreur(s) compatible(s) — la vente est la piste la plus porteuse."
    else:
        recommended = "location"
        reason = f"{len(location)} locataire(s) compatible(s) — la location capte plus de demande ici."

    aligned = recommended == lead_tx
    return {
        "ok": True,
        "recommended_transaction": recommended,
        "current_transaction": lead_tx,
        "aligned": aligned,
        "recommendation_reason": reason,
        "counts": {"vente": len(vente), "location": len(location), "total": len(matches)},
        "top_matches": matches[:top_n],
        "vente_matches": vente[:top_n],
        "location_matches": location[:top_n],
    }


def build_agency_match_index(leads: list[dict], clients: list[dict]) -> dict[int, dict]:
    """Résumé léger par lead pour les badges de liste (counts + reco), sans détail."""
    index: dict[int, dict] = {}
    if not clients:
        return index
    for lead in leads:
        lid = lead.get("id")
        if lid is None:
            continue
        summary = build_lead_matches(lead, clients, top_n=3)
        index[int(lid)] = {
            "recommended_transaction": summary["recommended_transaction"],
            "aligned": summary["aligned"],
            "counts": summary["counts"],
        }
    return index
