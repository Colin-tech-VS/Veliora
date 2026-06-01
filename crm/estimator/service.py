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

_FEATURES: list[tuple[str, str, float]] = [
    ("has_elevator", "Ascenseur", 0.025),
    ("has_parking", "Parking / box", 0.035),
    ("has_outdoor", "Balcon / terrasse / jardin", 0.05),
    ("has_view", "Belle vue", 0.03),
    ("noise_nuisance", "Nuisances (bruit, vis-à-vis…)", -0.04),
    ("prime_sector", "Quartier très recherché", 0.04),
]


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
    condition = (data.get("condition") or "standard").strip().lower()
    if condition not in {c[0] for c in _CONDITIONS}:
        condition = "standard"

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

    cond_adj = next((a for c, _, a in _CONDITIONS if c == condition), 0.0)
    if cond_adj:
        adjustments.append(
            {
                "key": "condition",
                "label": next(l for c, l, _ in _CONDITIONS if c == condition),
                "pct": round(cond_adj * 100, 1),
            }
        )
        total_adj += cond_adj

    if rooms is not None:
        if rooms >= 5:
            room_adj = 0.03
        elif rooms <= 1 and prop_type in ("appartement", "studio"):
            room_adj = -0.02
        else:
            room_adj = 0.0
        if room_adj:
            adjustments.append(
                {"key": "rooms", "label": f"{rooms} pièce(s)", "pct": round(room_adj * 100, 1)}
            )
            total_adj += room_adj

    for key, label, pct in _FEATURES:
        if data.get(key) in (True, "true", "1", 1, "on", "yes"):
            adjustments.append({"key": key, "label": label, "pct": round(pct * 100, 1)})
            total_adj += pct

    estimated = base_total * (1 + total_adj)
    conf_label, conf_key = _confidence_label(sample_count, bool(address and address != "—"))
    spread = _spread_pct(conf_key, sample_count)
    low = estimated * (1 - spread)
    high = estimated * (1 + spread)

    listing_price = int(lead.get("price") or 0)
    delta_listing = None
    if listing_price > 0:
        delta_listing = round((listing_price - estimated) / estimated * 100, 1)

    price_m2 = round(estimated / surface)
    listing_m2 = comp.get("listing_m2") or round(listing_price / surface) if listing_price else None

    return {
        "ok": True,
        "estimate_total": int(round(estimated)),
        "range_low": int(round(low)),
        "range_high": int(round(high)),
        "price_per_m2": price_m2,
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
        "condition": condition,
        "dvf_verdict": comp.get("verdict"),
        "dvf_verdict_label": comp.get("verdict_label"),
        "dvf_delta_pct": comp.get("delta_pct"),
        "dvf_app_url": DVF_APP_URL,
        "disclaimer": (
            "Estimation indicative Veliora — médiane des ventes DVF (Etalab) sur le secteur, "
            "ajustée selon vos critères. Ne remplace pas une visite, un diagnostic ni un avis "
            "de valeur certifié (ex. Meilleurs Agents, expert agréé)."
        ),
        "methodology": [
            f"Médiane DVF : {int(round(median_m2)):,} €/m² ({sample_count} ventes, {comp.get('dvf_reference_period') or 'période récente'})".replace(",", " "),
            f"Surface retenue : {surface:g} m² → base {int(round(base_total)):,} €".replace(",", " "),
            "Ajustements cumulés : "
            + (f"{round(total_adj * 100, 1):+}% ({len(adjustments)} critère(s))" if adjustments else "aucun"),
            f"Fourchette ±{int(spread * 100)}% (confiance {conf_label})",
        ],
    }


def estimator_form_schema() -> dict:
    return {
        "property_types": [{"value": v, "label": l} for v, l in _PROPERTY_TYPES],
        "conditions": [{"value": v, "label": l} for v, l, _ in _CONDITIONS],
        "features": [{"key": k, "label": l} for k, l, _ in _FEATURES],
    }
