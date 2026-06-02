"""Scoring pondéré du rapprochement annonce ↔ candidat (0..100 + justifications).

Le score est une somme pondérée de critères, normalisée sur les critères
réellement évaluables (on ne pénalise pas un candidat pour une donnée absente
de l'annonce). Chaque critère renseigné produit une « raison » lisible.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from crawler.address_match.features import ListingFeatures
from crawler.address_match.sources import AddressCandidate

# Poids par critère (somme indicative ≈ 100 si tout est évaluable).
WEIGHTS: dict[str, float] = {
    "city": 12,
    "neighborhood": 8,
    "surface": 22,
    "dpe_energy": 14,
    "dpe_climate": 6,
    "rooms": 8,
    "construction_period": 8,
    "property_type": 8,
    "price_m2_coherence": 6,
    "geo_coherence": 14,
    "photo_similarity": 6,
    "equipment": 4,
}

_DPE_ORDER = {c: i for i, c in enumerate("ABCDEFG")}


@dataclass
class ScoredCandidate:
    candidate: AddressCandidate
    score: int
    reasons: list[str]
    detail: dict[str, float]


def _surface_score(listing: float | None, cand: float | None) -> float | None:
    if not listing or not cand:
        return None
    diff = abs(listing - cand)
    if diff <= 1:
        return 1.0
    if diff <= 3:
        return 0.85
    if diff <= 6:
        return 0.6
    if diff <= 10:
        return 0.3
    return 0.0


def _norm(s: str | None) -> str:
    return (s or "").strip().lower()


def _dpe_distance_score(a: str | None, b: str | None) -> float | None:
    if not a or not b or a not in _DPE_ORDER or b not in _DPE_ORDER:
        return None
    d = abs(_DPE_ORDER[a] - _DPE_ORDER[b])
    return {0: 1.0, 1: 0.5}.get(d, 0.0)


def _haversine_m(lat1, lon1, lat2, lon2) -> float | None:
    if None in (lat1, lon1, lat2, lon2):
        return None
    r = 6371000.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def _geo_score(dist_m: float | None) -> float | None:
    if dist_m is None:
        return None
    if dist_m <= 50:
        return 1.0
    if dist_m <= 150:
        return 0.8
    if dist_m <= 400:
        return 0.5
    if dist_m <= 1000:
        return 0.2
    return 0.0


def score_candidate(
    feats: ListingFeatures, cand: AddressCandidate
) -> ScoredCandidate:
    """Note un candidat ; renvoie score 0..100 + raisons + détail par critère."""
    earned = 0.0
    possible = 0.0
    reasons: list[str] = []
    detail: dict[str, float] = {}

    def add(crit: str, ratio: float | None, reason_ok: str, reason_ko: str | None = None):
        nonlocal earned, possible
        if ratio is None:
            return
        w = WEIGHTS[crit]
        possible += w
        gain = w * ratio
        earned += gain
        detail[crit] = round(gain, 1)
        if ratio >= 0.6:
            reasons.append(reason_ok)
        elif reason_ko and ratio <= 0.1:
            reasons.append(reason_ko)

    # Ville
    if feats.city and cand.city:
        same = _norm(feats.city) in _norm(cand.city) or _norm(cand.city) in _norm(feats.city)
        add("city", 1.0 if same else 0.0,
            f"Même commune ({cand.city})", "Commune différente")

    # Quartier / secteur
    if feats.neighborhood and cand.address:
        hit = _norm(feats.neighborhood) in _norm(cand.address)
        add("neighborhood", 1.0 if hit else 0.0, f"Quartier cohérent ({feats.neighborhood})")

    # Surface
    s = _surface_score(feats.surface, cand.surface)
    if s is not None:
        add("surface", s,
            f"Surface proche ({cand.surface:g} m² vs {feats.surface:g} m²)",
            f"Surface éloignée ({cand.surface:g} m² vs {feats.surface:g} m²)")

    # DPE énergie / climat
    de = _dpe_distance_score(feats.dpe_energy_class, cand.dpe_energy_class)
    add("dpe_energy", de,
        f"Même classe énergie ({cand.dpe_energy_class})", "Classe énergie différente")
    dc = _dpe_distance_score(feats.dpe_climate_class, cand.dpe_climate_class)
    add("dpe_climate", dc, f"Même classe climat ({cand.dpe_climate_class})")

    # Pièces (approx. via niveaux pour maison)
    if feats.rooms and cand.rooms:
        diff = abs(feats.rooms - cand.rooms)
        add("rooms", 1.0 if diff == 0 else (0.5 if diff == 1 else 0.0),
            f"Même nombre de pièces ({cand.rooms})")

    # Période de construction (± 10 ans)
    if feats.construction_year and cand.construction_year:
        d = abs(feats.construction_year - cand.construction_year)
        ratio = 1.0 if d <= 3 else (0.6 if d <= 10 else (0.2 if d <= 25 else 0.0))
        add("construction_period", ratio,
            f"Même période de construction (~{cand.construction_year})")

    # Type de bien
    if feats.property_type and cand.property_type:
        add("property_type", 1.0 if feats.property_type == cand.property_type else 0.0,
            f"Même type ({cand.property_type})", "Type de bien différent")

    # Cohérence prix/m² (sanity check via DPE non dispo → neutre)
    if feats.price_per_m2 and cand.raw.get("market_m2"):
        ref = cand.raw["market_m2"]
        delta = abs(feats.price_per_m2 - ref) / max(ref, 1)
        add("price_m2_coherence", max(0.0, 1.0 - delta * 2),
            "Prix/m² cohérent avec le marché local")

    # Cohérence géographique (distance GPS)
    dist = _haversine_m(feats.latitude, feats.longitude, cand.latitude, cand.longitude)
    g = _geo_score(dist)
    if g is not None:
        add("geo_coherence", g,
            f"Position GPS proche (~{int(dist)} m)", "Position GPS éloignée")

    # Similarité photo (si métadonnées image disponibles)
    photo = (feats.image_meta or {}).get("match_ratio")
    if isinstance(photo, (int, float)):
        add("photo_similarity", float(photo), "Façade/photo concordante")

    # Cohérence des équipements
    eq_listing = [
        k for k in ("has_elevator", "has_parking", "has_cellar", "has_balcony",
                    "has_terrace", "has_pool")
        if getattr(feats, k)
    ]
    eq_cand = cand.raw.get("equipment") or []
    if eq_listing and eq_cand:
        inter = len(set(eq_listing) & set(eq_cand))
        add("equipment", inter / len(eq_listing), "Équipements concordants")

    if possible <= 0:
        return ScoredCandidate(cand, 0, ["Aucun critère comparable disponible"], detail)

    # Normalisation : score sur les critères réellement évaluables, atténué si
    # peu de critères ont pu être évalués (faible couverture = moins de confiance).
    coverage = min(1.0, possible / 60.0)
    raw_ratio = earned / possible
    score = int(round(raw_ratio * 100 * (0.55 + 0.45 * coverage)))
    score = max(0, min(100, score))
    if cand.parcel:
        reasons.append(f"Parcelle cadastrale {cand.parcel}")
    return ScoredCandidate(cand, score, reasons[:6], detail)


def rank_candidates(
    feats: ListingFeatures, candidates: list[AddressCandidate]
) -> list[ScoredCandidate]:
    scored = [score_candidate(feats, c) for c in candidates]
    scored.sort(key=lambda s: s.score, reverse=True)
    return scored
