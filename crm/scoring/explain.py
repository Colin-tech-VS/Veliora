"""Explicabilité et priorité."""

from __future__ import annotations

from typing import Any

from crm.scoring.mandate import MandateScoreResult


def priority_tier(score: int) -> str:
    if score >= 85:
        return "critique"
    if score >= 70:
        return "elevee"
    if score >= 50:
        return "moyenne"
    return "faible"


def build_score_explanation(
    lead: dict,
    result: MandateScoreResult,
) -> dict[str, Any]:
    score = result.score
    return {
        "mandate_score": score,
        "positive_factors": result.positive[:10],
        "negative_factors": result.negative[:8],
        "contributions": [
            {
                "key": c.key,
                "label": c.label,
                "points": c.points,
                "detail": c.detail,
            }
            for c in result.contributions
        ],
        "priority_tier": priority_tier(score),
        "summary_sentence": result.reason,
        "capped_reason": result.capped_reason,
        "tags": result.tags,
        "recommendation": _recommendation(score),
    }


def _recommendation(score: int) -> dict[str, str]:
    if score >= 85:
        return {
            "label": "Contacter dans les 24 h",
            "horizon": "24h",
            "urgency": "urgent",
            "detail": "Plusieurs signaux mandat — priorité immédiate.",
        }
    if score >= 65:
        return {
            "label": "Contacter sous 48 h",
            "horizon": "48h",
            "urgency": "high",
            "detail": "Bonne opportunité — planifier l'appel rapidement.",
        }
    if score >= 45:
        return {
            "label": "À traiter cette semaine",
            "horizon": "7j",
            "urgency": "medium",
            "detail": "Profil intéressant — qualifier au téléphone.",
        }
    return {
        "label": "À surveiller",
        "horizon": "—",
        "urgency": "low",
        "detail": "Peu de signaux — compléter la fiche ou relancer après crawl.",
    }
