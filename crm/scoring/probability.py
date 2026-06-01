"""Conversion du Score Mandat (0–100) en probabilité de signature du mandat.

Le score brut est une somme de points pondérés (cf. crm/scoring/mandate.py).
On le transforme ici en **% de chance de signer le mandat**, plus parlant pour
le conseiller : « 56 % de chance de signer » plutôt qu'un score abstrait.

Calibrage (sans données de conversion réelles → courbe conservatrice et
monotone) :
- une régression logistique mappe le score sur une probabilité ;
- un facteur de joignabilité corrige le résultat : on ne signe pas un mandat
  avec un vendeur qu'on ne peut pas joindre (ni téléphone, ni email).

Le plafond (~82 %) est volontaire : décrocher un mandat n'est jamais certain.
"""

from __future__ import annotations

import math

# Ancrages logistiques : score 0→~1 %, 50→~17 %, 71→41 %, 85→~56 %, 100→~69 %.
_LOGISTIC_CEILING = 0.82
_LOGISTIC_STEEPNESS = 0.062
_LOGISTIC_MIDPOINT = 71.0


def _has_value(raw) -> bool:
    return bool(raw and str(raw).strip() not in ("", "—", "-"))


def _logistic(score: float) -> float:
    try:
        return _LOGISTIC_CEILING / (
            1.0 + math.exp(-_LOGISTIC_STEEPNESS * (float(score) - _LOGISTIC_MIDPOINT))
        )
    except (OverflowError, ValueError):
        return 0.0


def _contactability_factor(lead: dict) -> float:
    """On ne signe que ce qu'on peut joindre."""
    if _has_value(lead.get("phone")):
        return 1.0
    if _has_value(lead.get("email")):
        return 0.82  # email seul : joignable mais moins direct
    return 0.5  # ni tel ni email → messagerie portail uniquement


def signature_band(probability: int) -> tuple[str, str]:
    """(libellé, ton) pour l'UI selon la probabilité."""
    if probability >= 55:
        return "Très élevée", "critical"
    if probability >= 40:
        return "Élevée", "high"
    if probability >= 25:
        return "Modérée", "medium"
    if probability >= 12:
        return "Faible", "low"
    return "Très faible", "low"


def signature_probability(lead: dict, score: int | None = None) -> dict:
    """% de chance de signer le mandat + bande + libellé prêt à afficher."""
    s = score if score is not None else (lead.get("mandate_score") or lead.get("score") or 0)
    try:
        s = float(s)
    except (TypeError, ValueError):
        s = 0.0

    raw = _logistic(s) * _contactability_factor(lead)
    pct = int(round(max(0.01, min(0.95, raw)) * 100))
    band, tone = signature_band(pct)
    return {
        "probability": pct,
        "band": band,
        "tone": tone,
        "label": f"{pct} % de chance de signer",
    }
