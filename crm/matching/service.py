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
    "appart": "appartement",
    "maison": "maison",
    "villa": "maison",
    "terrain": "terrain",
}

_MIN_DISPLAY_SCORE = 45
_CP_RE = re.compile(r"\b(\d{5})\b")


def _norm(s: str | None) -> str:
    s = (s or "").strip().lower()
    s = "".join(c for c in unicodedata.normalize("NFD", s) if unicodedata.category(c) != "Mn")
    return re.sub(r"\s+", " ", s)


def _strip_arrondissement(city: str) -> str:
    city = (city or "").strip()
    city = re.sub(r"\s+\d{1,2}(?:e|er|eme|ème)?\s*$", "", city, flags=re.I)
    city = re.sub(r"\s*\(\d{1,2}\)\s*$", "", city)
    return city.strip()


def _city_names_overlap(lead_city: str, target: str) -> bool:
    a = _strip_arrondissement(_norm(lead_city))
    b = _strip_arrondissement(_norm(target))
    if not a or not b:
        return False
    if a == b:
        return True
    return a in b or b in a


def _digits_only(s: str) -> str:
    return re.sub(r"\D", "", s or "")


def _postcode_token_match(lead_pc: str, token_digits: str) -> bool:
    """CP annonce vs token client (5 chiffres, 3 ou 2 pour secteur)."""
    if not lead_pc or not token_digits:
        return False
    lead_pc = lead_pc[:5]
    if len(token_digits) >= 5:
        return lead_pc == token_digits[:5] or lead_pc[:3] == token_digits[:3]
    if len(token_digits) == 3:
        return lead_pc.startswith(token_digits)
    if len(token_digits) == 2:
        return lead_pc.startswith(token_digits)
    return lead_pc.startswith(token_digits)


def _lead_location(lead: dict) -> tuple[str, str]:
    """Ville et code postal normalisés ; complète depuis l'adresse si besoin."""
    city = _norm(lead.get("city"))
    pc = _digits_only(lead.get("postcode") or "")[:5]

    addr = (lead.get("address") or "").strip()
    if addr:
        if not pc:
            m = _CP_RE.search(addr)
            if m:
                pc = m.group(1)
        if not city:
            m = re.search(
                r"\b(\d{5})\s+([A-Za-zÀ-ÿ][A-Za-zÀ-ÿ\s'\-]+?)\s*$",
                addr,
                flags=re.I,
            )
            if m:
                city = _norm(m.group(2))
            else:
                m2 = re.search(r"([A-Za-zÀ-ÿ][A-Za-zÀ-ÿ\s'\-]{2,})\s*$", addr)
                if m2 and not re.search(r"\d", m2.group(1)):
                    city = _norm(m2.group(1))
    return city, pc


def _guess_type(lead: dict) -> str:
    raw = _norm(lead.get("property_type") or "")
    if raw in _TYPE_ALIASES:
        return _TYPE_ALIASES[raw]
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
    cities = [str(c).strip() for c in (client.get("cities") or []) if c and str(c).strip()]
    if not cities:
        return True, False

    lead_city, lead_pc = _lead_location(lead)
    if not lead_city and not lead_pc:
        return True, False

    for raw in cities:
        norm = _norm(raw)
        digits = _digits_only(raw)
        if digits and lead_pc and _postcode_token_match(lead_pc, digits):
            return True, True
        if lead_city and norm and _city_names_overlap(lead_city, norm):
            return True, True
    return False, True


def score_client_for_lead(lead: dict, client: dict) -> dict | None:
    """Note de compatibilité 0..100 + raisons. None si incompatible (ville/type/surface/pièces)."""
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
    score = 42.0
    if city_constrained:
        score += 15
        reasons.append("Secteur recherché")

    # Surface : élimination seulement si l'annonce a une surface connue trop faible
    surface = lead.get("surface")
    if surface is not None:
        try:
            surface = float(surface)
        except (TypeError, ValueError):
            surface = None
    smin = client.get("surface_min")
    if smin and surface is not None:
        if surface + 0.5 >= float(smin):
            score += 12
            reasons.append(f"Surface ≥ {int(float(smin))} m²")
        else:
            return None

    rooms = _lead_rooms(lead)
    rmin = client.get("rooms_min")
    if rmin and rooms is not None:
        if rooms >= int(rmin):
            score += 10
            reasons.append(f"{rooms} pièces ≥ {rmin}")
        else:
            return None

    price = int(lead.get("price") or 0)
    bmin = client.get("budget_min")
    bmax = client.get("budget_max")
    price_comparable = lead_tx == expected_tx and price > 0
    in_budget = None
    if price_comparable and (bmin or bmax):
        lo = float(bmin or 0)
        hi = float(bmax or 0)
        if hi and price > hi * 1.10:
            in_budget = False
            reasons.append("Au-dessus du budget")
            score -= 18
        elif lo and price < lo * 0.70:
            in_budget = False
            reasons.append("Sous la cible budget")
            score -= 6
        else:
            in_budget = True
            score += 23
            reasons.append("Dans le budget")
    elif price_comparable and price > 0:
        score += 8
        reasons.append("Prix renseigné")
    elif not price_comparable and (bmin or bmax):
        reasons.append("Prix non comparable (transaction différente)")

    if lead_tx == expected_tx:
        score += 5

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


def _active_clients(clients: list[dict]) -> list[dict]:
    return [
        c
        for c in clients or []
        if (c.get("status") or "actif") in ("actif", "", None)
    ]


def _diagnose_lead_matches(lead: dict, clients: list[dict]) -> dict:
    active = _active_clients(clients)
    hints: list[str] = []
    if not active:
        return {
            "hints": [
                "Aucun acquéreur/locataire actif : créez des fiches dans Acheteurs / Locataires."
            ],
            "active_clients": 0,
        }

    lead_city, lead_pc = _lead_location(lead)
    loc_label = " ".join(
        p for p in (lead.get("city") or lead_city, lead.get("postcode") or lead_pc) if p
    ) or "localisation non renseignée sur l'annonce"

    type_block = city_block = criteria_block = score_low = 0
    for client in active:
        if not _type_compatible(_guess_type(lead), client.get("property_type")):
            type_block += 1
            continue
        ok, _ = _city_compatible(lead, client)
        if not ok:
            city_block += 1
            continue
        scored = score_client_for_lead(lead, client)
        if scored is None:
            criteria_block += 1
        elif scored["score"] < _MIN_DISPLAY_SCORE:
            score_low += 1

    if type_block == len(active):
        hints.append("Le type de bien ne correspond à aucun profil (ex. maison vs appartement).")
    if city_block and city_block >= max(1, len(active) // 2):
        hints.append(
            f"Les villes/CP clients ne recoupent pas l'annonce ({loc_label}). "
            "Ajoutez le code postal (ex. 69003) ou le nom de ville exact dans les fiches clients."
        )
    if criteria_block and not hints:
        hints.append(
            "Surface ou nombre de pièces insuffisant par rapport aux minimums clients."
        )
    if score_low and not hints:
        hints.append("Des profils sont proches mais le score reste faible (budget trop serré ?).")
    if not hints:
        hints.append(
            "Aucun profil actif ne recoupe cette annonce : vérifiez segment (acheteur/locataire), "
            "budget et critères."
        )

    return {"hints": hints[:4], "active_clients": len(active)}


def _diagnose_client_matches(client: dict, leads: list[dict]) -> dict:
    segment = (client.get("segment") or "acheteur").lower()
    expected_tx = _SEGMENT_TX.get(segment, "vente")
    hints: list[str] = []

    pool = [
        l
        for l in leads or []
        if (l.get("status") or "").lower() != "retire"
    ]
    if not pool:
        return {"hints": ["Aucune annonce dans le portefeuille (lancez la veille)."], "leads_total": 0}

    tx_pool = [
        l for l in pool if (l.get("transaction_type") or "vente").lower() == expected_tx
    ]
    if not tx_pool:
        other = "location" if expected_tx == "vente" else "vente"
        hints.append(
            f"Aucune annonce classée en {expected_tx} — {len(pool)} annonce(s) en {other}. "
            f"Un·e {segment} ne matche que les biens en {expected_tx}."
        )
        return {"hints": hints[:4], "leads_total": len(pool), "leads_matching_tx": 0}

    cities = [c for c in (client.get("cities") or []) if c]
    if cities:
        located = 0
        city_hits = 0
        for lead in tx_pool:
            lc, lp = _lead_location(lead)
            if lc or lp:
                located += 1
                if _city_compatible(lead, client)[0]:
                    city_hits += 1
        if located and city_hits == 0:
            hints.append(
                f"Aucune annonce {expected_tx} ne correspond aux villes/CP : "
                f"{', '.join(str(c) for c in cities[:5])}."
            )

    near = 0
    for lead in tx_pool:
        s = score_client_for_lead(lead, client)
        if s and s["score"] >= _MIN_DISPLAY_SCORE:
            near += 1
    if not near and not hints:
        hints.append(
            "Élargissez le budget, baissez surface/pièces min, ou retirez le type de bien "
            "pour ouvrir le matching."
        )

    return {
        "hints": hints[:4],
        "leads_total": len(pool),
        "leads_matching_tx": len(tx_pool),
    }


def build_lead_matches(lead: dict, clients: list[dict], *, top_n: int = 8) -> dict:
    """Rapproche un bien de tous les clients ; recommande la transaction la plus porteuse."""
    matches: list[dict] = []
    for client in _active_clients(clients):
        scored = score_client_for_lead(lead, client)
        if scored and scored["score"] >= _MIN_DISPLAY_SCORE:
            matches.append(scored)

    matches.sort(key=lambda m: m["score"], reverse=True)

    vente = [m for m in matches if m["expected_transaction"] == "vente"]
    location = [m for m in matches if m["expected_transaction"] == "location"]

    def _strength(rows: list[dict]) -> float:
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
    out: dict = {
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
    if not matches:
        out["diagnostics"] = _diagnose_lead_matches(lead, clients)
    return out


def demand_counts(lead: dict, clients: list[dict]) -> dict:
    """Compteurs de demande pour le scoring : compatibles par transaction + 'strong'."""
    out = {"vente": 0, "location": 0, "total": 0, "strong": 0}
    for client in _active_clients(clients):
        scored = score_client_for_lead(lead, client)
        if not scored or scored["score"] < _MIN_DISPLAY_SCORE:
            continue
        out["total"] += 1
        tx = scored["expected_transaction"]
        out[tx] = out.get(tx, 0) + 1
        if scored["score"] >= 65:
            out["strong"] += 1
    return out


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


def build_client_matches(
    client: dict,
    leads: list[dict],
    *,
    top_n: int = 12,
    min_score: int = _MIN_DISPLAY_SCORE,
) -> dict:
    """Liste les annonces du portefeuille compatibles avec un acheteur/locataire donné."""
    segment = (client.get("segment") or "acheteur").lower()
    expected_tx = _SEGMENT_TX.get(segment, "vente")

    matches: list[dict] = []
    for lead in leads or []:
        status = (lead.get("status") or "").lower()
        if status == "retire":
            continue
        lead_tx = (lead.get("transaction_type") or "vente").lower()
        if lead_tx != expected_tx:
            continue

        scored = score_client_for_lead(lead, client)
        if not scored or scored["score"] < min_score:
            continue

        lead_city, lead_pc = _lead_location(lead)
        matches.append({
            "lead_id": lead.get("id"),
            "score": scored["score"],
            "in_budget": scored.get("in_budget"),
            "reasons": scored.get("reasons", []),
            "title": lead.get("listing_title") or lead.get("address") or "Annonce",
            "address": lead.get("address") or "",
            "city": lead.get("city") or lead_city,
            "postcode": lead.get("postcode") or lead_pc,
            "price": lead.get("price"),
            "transaction_type": lead_tx,
            "price_period": lead.get("price_period"),
            "surface": lead.get("surface"),
            "rooms": lead.get("rooms"),
            "mandate_score": lead.get("mandate_score") or 0,
            "listing_type": lead.get("listing_type") or lead.get("type"),
            "source_url": lead.get("source_url"),
            "image_url": lead.get("listing_image_url"),
            "published_at": lead.get("published_at"),
        })

    matches.sort(key=lambda m: (m["score"], m.get("mandate_score") or 0), reverse=True)

    in_budget = sum(1 for m in matches if m.get("in_budget") is True)
    out: dict = {
        "ok": True,
        "client_id": client.get("id"),
        "segment": segment,
        "expected_transaction": expected_tx,
        "counts": {
            "total": len(matches),
            "in_budget": in_budget,
            "strong": sum(1 for m in matches if m["score"] >= 65),
        },
        "top_matches": matches[:top_n],
    }
    if not matches:
        out["diagnostics"] = _diagnose_client_matches(client, leads)
    return out
