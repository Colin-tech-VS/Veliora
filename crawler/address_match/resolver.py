"""Orchestrateur du rapprochement d'adresse.

Pipeline (post-processing, indépendant de la source) :
  1. Features structurées de l'annonce (déjà extraites en amont).
  2. Résolution de la commune (code INSEE) via le module DVF existant.
  3. Génération de candidats depuis les sources publiques (DPE, BAN).
  4. Scoring pondéré + classement.
  5. Enrichissement parcellaire du meilleur candidat (cadastre).
  6. Sortie : adresse_probable + score_confiance + candidats justifiés.

Garde-fou : si aucune source ne renvoie de candidat exploitable, on renvoie
`adresse_probable = None` (score 0). **Le système n'invente jamais d'adresse.**
"""

from __future__ import annotations

import logging
from typing import Any

from crawler.address_match import sources
from crawler.address_match.features import ListingFeatures
from crawler.address_match.scoring import rank_candidates

logger = logging.getLogger(__name__)

# Seuil minimal pour proposer une adresse « probable » en tête de sortie.
PROBABLE_MIN_SCORE = 55
MAX_CANDIDATES_OUT = 5
CADASTRE_ENRICH_MIN_SCORE = 70


def _resolve_commune(feats: ListingFeatures) -> dict[str, Any]:
    """Code INSEE + CP + ville via le module DVF (BAN + geo.api.gouv.fr)."""
    from crm.dvf import geocode_address, resolve_commune_code

    citycode = postcode = city = None
    lat = feats.latitude
    lon = feats.longitude

    query = feats.partial_address or feats.title or ""
    if query or feats.city:
        geo = geocode_address(query, feats.city or "")
        if geo:
            citycode = geo.get("citycode")
            postcode = geo.get("postcode")
            city = geo.get("city")
            lat = lat or geo.get("lat")
            lon = lon or geo.get("lon")

    if not citycode and (feats.city or feats.postcode):
        resolved = resolve_commune_code(feats.city or "", feats.postcode)
        if resolved:
            citycode = resolved.get("code")
            city = city or resolved.get("nom")

    return {
        "citycode": citycode,
        "postcode": postcode or feats.postcode,
        "city": city or feats.city,
        "lat": lat,
        "lon": lon,
    }


def _market_m2(commune: dict, feats: ListingFeatures) -> int | None:
    """Médiane DVF €/m² locale (réutilise le cache DVF existant)."""
    if not commune.get("citycode"):
        return None
    try:
        from crm.dvf import compute_price_stats

        tlocal = "Maison" if feats.property_type == "maison" else "Appartement"
        stats = compute_price_stats(
            commune["citycode"], tlocal, postcode=commune.get("postcode")
        )
        if stats and stats.get("available"):
            return int(stats["median_m2"])
    except Exception as exc:
        logger.debug("market_m2: %s", str(exc)[:120])
    return None


def resolve_address(
    feats: ListingFeatures,
    *,
    enrich_cadastre: bool = True,
    max_candidates: int = MAX_CANDIDATES_OUT,
) -> dict[str, Any]:
    """Renvoie la sortie de rapprochement au format spécifié."""
    commune = _resolve_commune(feats)
    feats.latitude = feats.latitude or commune.get("lat")
    feats.longitude = feats.longitude or commune.get("lon")

    # 1) Candidats DPE (source la plus discriminante)
    raw_candidates: list[sources.AddressCandidate] = []
    raw_candidates += sources.dpe_candidates(
        citycode=commune.get("citycode"),
        postcode=commune.get("postcode"),
        city=commune.get("city"),
        surface=feats.surface,
        energy_class=feats.dpe_energy_class,
    )

    # 2) Candidats BAN si une adresse partielle / rue figure dans l'annonce
    if feats.partial_address:
        raw_candidates += sources.ban_candidates(
            feats.partial_address,
            postcode=commune.get("postcode"),
            citycode=commune.get("citycode"),
        )

    # 3) Dédup + contexte marché pour la cohérence prix/m²
    market = _market_m2(commune, feats)
    seen: set[str] = set()
    deduped: list[sources.AddressCandidate] = []
    for c in raw_candidates:
        k = c.key()
        if k in seen:
            continue
        seen.add(k)
        if market:
            c.raw["market_m2"] = market
        deduped.append(c)

    # 4) Scoring + classement
    scored = rank_candidates(feats, deduped)

    # 5) Enrichissement parcellaire (cadastre) du meilleur candidat seulement
    if enrich_cadastre and scored and scored[0].score >= CADASTRE_ENRICH_MIN_SCORE:
        top = scored[0].candidate
        if top.latitude and top.longitude and not top.parcel:
            top.parcel = sources.cadastre_parcel(top.latitude, top.longitude)
            if top.parcel:
                scored[0].reasons.append(f"Parcelle cadastrale {top.parcel}")

    candidates_out = [
        {
            "adresse": s.candidate.address,
            "score": s.score,
            "source": s.candidate.source,
            "latitude": s.candidate.latitude,
            "longitude": s.candidate.longitude,
            "parcelle": s.candidate.parcel,
            "raisons": s.reasons,
        }
        for s in scored[:max_candidates]
    ]

    top_score = scored[0].score if scored else 0
    probable = (
        scored[0].candidate.address
        if scored and top_score >= PROBABLE_MIN_SCORE
        else None
    )

    return {
        "ok": True,
        "adresse_probable": probable,
        "score_confiance": top_score if probable else 0,
        "candidats": candidates_out,
        "commune": {
            "ville": commune.get("city"),
            "code_postal": commune.get("postcode"),
            "code_insee": commune.get("citycode"),
        },
        "sources_interrogees": sorted({c.source for c in deduped}) or [],
        "nb_candidats": len(deduped),
        "note": (
            None
            if probable
            else "Aucune adresse au-dessus du seuil de confiance — candidats fournis sans certitude."
        ),
    }


def resolve_address_for_lead(lead) -> dict[str, Any]:
    """Construit les features depuis un lead (dict ou LeadData) puis résout."""
    if hasattr(lead, "raw_extras"):
        stored = (lead.raw_extras or {}).get("listing_features")
        feats = ListingFeatures.from_dict(stored) if stored else None
        if feats is None:
            from crawler.address_match.features import extract_listing_features

            feats = extract_listing_features(lead, None)
    else:
        feats = _features_from_lead_row(lead)
    return resolve_address(feats)


def _features_from_lead_row(row: dict) -> ListingFeatures:
    """Reconstruit des features depuis une ligne `leads` (lecture API)."""
    stored = row.get("listing_features")
    if isinstance(stored, dict):
        return ListingFeatures.from_dict(stored)
    return ListingFeatures(
        title=row.get("listing_title") or row.get("property_title"),
        city=row.get("city"),
        postcode=row.get("postcode"),
        neighborhood=row.get("sector"),
        partial_address=row.get("address"),
        surface=row.get("surface"),
        price=row.get("price"),
        price_per_m2=(
            int(row["price"] / row["surface"])
            if row.get("price") and row.get("surface")
            else None
        ),
        published_at=row.get("published_at"),
        agency=row.get("agency"),
        latitude=row.get("latitude"),
        longitude=row.get("longitude"),
    )
