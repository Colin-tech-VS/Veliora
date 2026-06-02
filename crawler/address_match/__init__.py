"""Rapprochement d'adresse standardisé pour tous les crawlers Veliora.

Pipeline post-scraping, indépendant de la source :
  features structurées → sources publiques (DPE/BAN/DVF/cadastre) →
  candidats → scoring pondéré → adresse probable + confiance + justifications.

Le système n'invente jamais d'adresse : il ne renvoie que des candidats issus
de données publiques, ordonnés par score de confiance.
"""

from crawler.address_match.features import (
    ListingFeatures,
    apply_features_to_lead,
    extract_listing_features,
)
from crawler.address_match.queue import AddressMatchQueue, resolve_and_store_lead_address
from crawler.address_match.resolver import resolve_address, resolve_address_for_lead
from crawler.address_match.storage import get_address_match, save_address_match

__all__ = [
    "ListingFeatures",
    "apply_features_to_lead",
    "extract_listing_features",
    "AddressMatchQueue",
    "resolve_and_store_lead_address",
    "resolve_address",
    "resolve_address_for_lead",
    "get_address_match",
    "save_address_match",
]
