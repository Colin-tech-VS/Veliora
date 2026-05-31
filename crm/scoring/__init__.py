"""Scoring Mandat Veliora — grille explicable et calibrage par agence."""

from crm.scoring.recalc import enrich_lead_scores
from crm.scoring.mandate import compute_mandate_score, MandateScoreResult

__all__ = [
    "enrich_lead_scores",
    "compute_mandate_score",
    "MandateScoreResult",
]
