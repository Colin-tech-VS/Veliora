"""Estimation de valeur vénale indicative — ventes DVF Etalab + ajustements métier."""

from __future__ import annotations

import re
from typing import Any

from crm.dvf import compare_listing_to_dvf, DVF_APP_URL

_PROPERTY_TYPES = (
    ("appartement", "Appartement"),
    ("maison", "Maison"),
    ("studio", "Studio"),
    ("terrain", "Terrain"),
    ("autre", "Autre"),
)

_CONDITIONS = (
    ("neuf", "Neuf / récent", 0.06),
    ("bon", "Bon état", 0.03),
    ("standard", "Standard", 0.0),
    ("rafraichir", "À rafraîchir", -0.05),
    ("renover", "À rénover", -0.10),
)

# Classe énergie (DPE). Une passoire thermique (F/G) décote fortement depuis
# l'interdiction progressive de location et le malus à la revente.
_DPE_GRADES = (
    ("A", "DPE A", 0.04),
    ("B", "DPE B", 0.025),
    ("C", "DPE C", 0.01),
    ("D", "DPE D", 0.0),
    ("E", "DPE E", -0.02),
    ("F", "DPE F (passoire)", -0.06),
    ("G", "DPE G (passoire)", -0.09),
)

# Orientation principale du bien.
_EXPOSURES = (
    ("sud", "Plein sud", 0.025),
    ("sud_ouest", "Sud / Ouest", 0.02),
    ("traversant", "Traversant", 0.02),
    ("est_ouest", "Est / Ouest", 0.0),
    ("nord", "Nord", -0.025),
)

# Période de construction (qualité de bâti / charges).
_CONSTRUCTION_PERIODS = (
    ("avant_1949", "Avant 1949 (ancien)", 0.0),
    ("1949_1974", "1949–1974", -0.025),
    ("1975_2000", "1975–2000", 0.0),
    ("apres_2000", "Après 2000 (récent)", 0.025),
)

_FEATURES: list[tuple[str, str, float]] = [
    ("has_elevator", "Ascenseur", 0.025),
    ("has_parking", "Parking / box", 0.035),
    ("has_outdoor", "Balcon / terrasse / jardin", 0.05),
    ("has_cellar", "Cave / cellier", 0.015),
    ("has_view", "Belle vue", 0.03),
    ("bright", "Très lumineux", 0.02),
    ("recent_renovation", "Rénovation récente", 0.04),
    ("noise_nuisance", "Nuisances (bruit, vis-à-vis…)", -0.04),
    ("prime_sector", "Quartier très recherché", 0.04),
]

# Honoraires d'agence par défaut appliqués pour passer du net vendeur au prix FAI.
_DEFAULT_COMMISSION_PCT = 5.0
_MAX_COMMISSION_PCT = 12.0


def _guess_property_type(lead: dict, override: str | None) -> str:
    if override and override in {p[0] for p in _PROPERTY_TYPES}:
        return override
    title = (lead.get("property_title") or lead.get("listing_title") or lead.get("address") or "").lower()
    if "studio" in title:
        return "studio"
    if any(w in title for w in ("maison", "villa", "pavillon", "longère")):
        return "maison"
    if "terrain" in title:
        return "terrain"
    if any(w in title for w in ("appart", "duplex", "loft", "t1", "t2", "t3", "t4", "f1", "f2")):
        return "appartement"
    return "appartement"


def _type_local_for_dvf(prop_type: str) -> str:
    return {
        "appartement": "Appartement",
        "studio": "Appartement",
        "maison": "Maison",
        "terrain": "Terrain",
    }.get(prop_type, "Appartement")


def _parse_rooms(val: Any) -> int | None:
    if val is None or val == "":
        return None
    try:
        n = int(float(str(val).replace(",", ".")))
        return n if 0 < n < 20 else None
    except (TypeError, ValueError):
        return None


def _parse_floor(val: Any) -> int | None:
    """Étage : 0 = rez-de-chaussée, accepte 'rdc'/'rez'."""
    if val is None or val == "":
        return None
    s = str(val).strip().lower()
    if s in ("rdc", "rez", "rez-de-chaussée", "rez de chaussee"):
        return 0
    try:
        n = int(float(s.replace(",", ".")))
        return n if -2 <= n <= 60 else None
    except (TypeError, ValueError):
        return None


def _parse_commission(val: Any) -> float:
    if val is None or val == "":
        return _DEFAULT_COMMISSION_PCT
    try:
        pct = float(str(val).replace(",", ".").replace("%", "").strip())
    except (TypeError, ValueError):
        return _DEFAULT_COMMISSION_PCT
    return max(0.0, min(pct, _MAX_COMMISSION_PCT))


def _floor_adjustment(floor: int | None, has_elevator: bool, prop_type: str) -> tuple[float, str | None]:
    """Décote/surcote liée à l'étage (appartements / studios uniquement)."""
    if floor is None or prop_type not in ("appartement", "studio"):
        return 0.0, None
    if floor == 0:
        return -0.03, "Rez-de-chaussée"
    if not has_elevator and floor >= 3:
        return -0.035, f"{floor}e étage sans ascenseur"
    if has_elevator and floor >= 2:
        return 0.015, f"{floor}e étage avec ascenseur"
    return 0.0, None


def _confidence_label(sample_count: int, has_address: bool) -> tuple[str, str]:
    if sample_count >= 40 and has_address:
        return "élevée", "high"
    if sample_count >= 15:
        return "moyenne", "medium"
    return "faible", "low"


def _spread_pct(confidence: str, sample_count: int) -> float:
    if confidence == "high":
        return 0.05
    if confidence == "medium":
        return 0.08
    return 0.12 if sample_count >= 8 else 0.15


def build_price_estimate(lead: dict, inputs: dict | None = None) -> dict:
    """
    Estimation indicative (fourchette basse / médiane / haute) en €.
    S'appuie sur la médiane DVF locale (ventes réelles) et des coefficients d'ajustement.

    Deux prix sont renvoyés :
      • net vendeur  → valeur du bien acté (base DVF), comparable aux estimations
        type Meilleurs Agents ;
      • FAI (frais d'agence inclus) → net vendeur + honoraires d'agence, soit le
        prix de présentation de l'annonce.
    """
    data = inputs or {}
    tx = (lead.get("transaction_type") or "vente").lower()
    if tx == "location":
        return {"ok": False, "reason": "L'estimateur s'applique aux biens en vente."}

    try:
        surface = float(data.get("surface") or lead.get("surface") or 0)
    except (TypeError, ValueError):
        surface = 0.0
    if surface <= 0:
        return {"ok": False, "reason": "Surface habitable requise (m²)."}

    prop_type = _guess_property_type(lead, (data.get("property_type") or "").strip() or None)
    rooms = _parse_rooms(data.get("rooms"))
    floor = _parse_floor(data.get("floor"))
    condition = (data.get("condition") or "standard").strip().lower()
    if condition not in {c[0] for c in _CONDITIONS}:
        condition = "standard"
    dpe = (data.get("dpe") or "").strip().upper()
    if dpe not in {g[0] for g in _DPE_GRADES}:
        dpe = ""
    exposure = (data.get("exposure") or "").strip().lower()
    if exposure not in {e[0] for e in _EXPOSURES}:
        exposure = ""
    construction = (data.get("construction_period") or "").strip().lower()
    if construction not in {c[0] for c in _CONSTRUCTION_PERIODS}:
        construction = ""
    commission_pct = _parse_commission(data.get("commission_pct"))

    address = (data.get("address") or lead.get("address") or "").strip()
    city = (data.get("city") or lead.get("city") or "").strip()
    postcode = (data.get("postcode") or lead.get("postcode") or "").strip() or None
    sector = (data.get("sector") or lead.get("sector") or lead.get("dvf_sector") or "").strip() or None

    comp = compare_listing_to_dvf(
        int(lead.get("price") or 0) or int(surface * 3500),
        surface,
        address,
        city,
        sector=sector,
        postcode=postcode,
        published_at=lead.get("published_at"),
        transaction_type="vente",
        type_local=_type_local_for_dvf(prop_type),
    )

    if not comp.get("available"):
        return {
            "ok": False,
            "reason": comp.get("reason") or "Données DVF indisponibles pour ce secteur.",
            "dvf_app_url": DVF_APP_URL,
        }

    median_m2 = float(comp["dvf_median_m2"])
    sample_count = int(comp.get("dvf_sample_count") or 0)
    base_total = median_m2 * surface

    adjustments: list[dict] = []
    total_adj = 0.0

    def _add(key: str, label: str, value: float) -> None:
        nonlocal total_adj
        if value:
            adjustments.append({"key": key, "label": label, "pct": round(value * 100, 1)})
            total_adj += value

    cond_adj = next((a for c, _, a in _CONDITIONS if c == condition), 0.0)
    _add("condition", next(l for c, l, _ in _CONDITIONS if c == condition), cond_adj)

    if rooms is not None:
        if rooms >= 5:
            _add("rooms", f"{rooms} pièces (grand)", 0.03)
        elif rooms <= 1 and prop_type in ("appartement", "studio"):
            _add("rooms", f"{rooms} pièce", -0.02)

    has_elevator = data.get("has_elevator") in (True, "true", "1", 1, "on", "yes")
    floor_adj, floor_label = _floor_adjustment(floor, has_elevator, prop_type)
    if floor_label:
        _add("floor", floor_label, floor_adj)

    if dpe:
        _add("dpe", next(l for g, l, _ in _DPE_GRADES if g == dpe), next(a for g, _, a in _DPE_GRADES if g == dpe))

    if exposure:
        _add("exposure", next(l for e, l, _ in _EXPOSURES if e == exposure), next(a for e, _, a in _EXPOSURES if e == exposure))

    if construction:
        _add(
            "construction_period",
            next(l for c, l, _ in _CONSTRUCTION_PERIODS if c == construction),
            next(a for c, _, a in _CONSTRUCTION_PERIODS if c == construction),
        )

    for key, label, pct in _FEATURES:
        if data.get(key) in (True, "true", "1", 1, "on", "yes"):
            _add(key, label, pct)

    # Prix net vendeur = base DVF (valeur acté du bien) ajustée des critères.
    net_vendeur = base_total * (1 + total_adj)
    conf_label, conf_key = _confidence_label(sample_count, bool(address and address != "—"))
    spread = _spread_pct(conf_key, sample_count)
    low = net_vendeur * (1 - spread)
    high = net_vendeur * (1 + spread)

    # Prix FAI (frais d'agence inclus) = net vendeur + honoraires.
    commission_factor = 1 + commission_pct / 100.0
    fai = net_vendeur * commission_factor
    commission_amount = fai - net_vendeur
    low_fai = low * commission_factor
    high_fai = high * commission_factor

    listing_price = int(lead.get("price") or 0)
    delta_listing = None
    if listing_price > 0:
        delta_listing = round((listing_price - net_vendeur) / net_vendeur * 100, 1)

    price_m2 = round(net_vendeur / surface)
    price_m2_fai = round(fai / surface)
    listing_m2 = comp.get("listing_m2") or round(listing_price / surface) if listing_price else None

    return {
        "ok": True,
        # Net vendeur (rétro-compatibilité : estimate_total = net vendeur).
        "estimate_total": int(round(net_vendeur)),
        "estimate_net_vendeur": int(round(net_vendeur)),
        "range_low": int(round(low)),
        "range_high": int(round(high)),
        "price_per_m2": price_m2,
        # FAI (frais d'agence inclus).
        "estimate_fai": int(round(fai)),
        "range_low_fai": int(round(low_fai)),
        "range_high_fai": int(round(high_fai)),
        "price_per_m2_fai": price_m2_fai,
        "commission_pct": round(commission_pct, 2),
        "commission_amount": int(round(commission_amount)),
        "base_dvf_total": int(round(base_total)),
        "median_m2": int(round(median_m2)),
        "listing_m2": listing_m2,
        "listing_price": listing_price or None,
        "delta_vs_estimate_pct": delta_listing,
        "adjustments": adjustments,
        "adjustments_total_pct": round(total_adj * 100, 1),
        "confidence": conf_key,
        "confidence_label": conf_label,
        "sample_count": sample_count,
        "reference_period": comp.get("dvf_reference_period") or "24 derniers mois",
        "commune": comp.get("commune") or city,
        "sector": comp.get("sector") or sector,
        "property_type": prop_type,
        "surface": surface,
        "rooms": rooms,
        "floor": floor,
        "condition": condition,
        "dpe": dpe or None,
        "exposure": exposure or None,
        "construction_period": construction or None,
        "dvf_verdict": comp.get("verdict"),
        "dvf_verdict_label": comp.get("verdict_label"),
        "dvf_delta_pct": comp.get("delta_pct"),
        "dvf_app_url": DVF_APP_URL,
        "disclaimer": (
            "Estimation indicative Veliora — médiane des ventes DVF (Etalab) sur le secteur, "
            "ajustée selon vos critères. Le prix net vendeur correspond à la valeur actée du bien "
            "(comparable à un avis Meilleurs Agents) ; le prix FAI inclut les honoraires d'agence. "
            "Ne remplace pas une visite, un diagnostic ni un avis de valeur certifié."
        ),
        "methodology": [
            f"Médiane DVF : {int(round(median_m2)):,} €/m² ({sample_count} ventes, {comp.get('dvf_reference_period') or 'période récente'})".replace(",", " "),
            f"Surface retenue : {surface:g} m² → base {int(round(base_total)):,} €".replace(",", " "),
            "Ajustements cumulés : "
            + (f"{round(total_adj * 100, 1):+}% ({len(adjustments)} critère(s))" if adjustments else "aucun"),
            f"Net vendeur (acté) : {int(round(net_vendeur)):,} €".replace(",", " "),
            f"FAI = net vendeur + {commission_pct:g}% honoraires → {int(round(fai)):,} €".replace(",", " "),
            f"Fourchette ±{int(spread * 100)}% (confiance {conf_label})",
        ],
    }


def estimator_form_schema() -> dict:
    return {
        "property_types": [{"value": v, "label": l} for v, l in _PROPERTY_TYPES],
        "conditions": [{"value": v, "label": l} for v, l, _ in _CONDITIONS],
        "dpe_grades": [{"value": v, "label": l} for v, l, _ in _DPE_GRADES],
        "exposures": [{"value": v, "label": l} for v, l, _ in _EXPOSURES],
        "construction_periods": [{"value": v, "label": l} for v, l, _ in _CONSTRUCTION_PERIODS],
        "features": [{"key": k, "label": l} for k, l, _ in _FEATURES],
        "default_commission_pct": _DEFAULT_COMMISSION_PCT,
    }
