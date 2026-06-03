"""Constantes CRM partagées (API, auth) — une seule source de vérité."""

from __future__ import annotations

# Incrémenter quand le contrat /api/health ou les routes CRM changent.
API_VERSION = 8

# Clé localStorage / cookie (veliora_* ; propscout_* conservé pour migration).
AUTH_TOKEN_KEY = "veliora_token"
AUTH_TOKEN_KEY_LEGACY = "propscout_token"
AUTH_USER_KEY = "veliora_user"
AUTH_USER_KEY_LEGACY = "propscout_user"

AUTH_TOKEN_KEYS = (AUTH_TOKEN_KEY, AUTH_TOKEN_KEY_LEGACY)

# Champs modifiables via PATCH /api/leads/{id} (aligné crawler.storage.patch_lead).
LEAD_PATCH_FIELDS = frozenset({
    "pipeline",
    "status",
    "notes",
    "next_follow_up",
    "first_name",
    "last_name",
    "phone",
    "email",
    "address",
    "city",
    "postcode",
    "sector",
    "surface",
    "price",
    "type",
    "listing_type",
    "agency",
    "source_url",
    "transaction_type",
})
