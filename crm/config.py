"""Configuration publique site / produit (variables d'environnement)."""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

_ROOT = Path(__file__).resolve().parents[1]
load_dotenv(_ROOT / ".env")

LEGAL_COMPANY_NAME = os.getenv("LEGAL_COMPANY_NAME", "Veliora")
LEGAL_SIRET = os.getenv("LEGAL_SIRET", "")
LEGAL_ADDRESS = os.getenv("LEGAL_ADDRESS", "")
LEGAL_HOSTING = os.getenv("LEGAL_HOSTING", "")
LEGAL_EMAIL = os.getenv("LEGAL_EMAIL", "contact@veliora.fr")
SUPPORT_EMAIL = os.getenv("SUPPORT_EMAIL", LEGAL_EMAIL)
DEMO_BOOKING_URL = os.getenv("DEMO_BOOKING_URL", f"mailto:{SUPPORT_EMAIL}?subject=Démo%20Veliora")
SUPPORT_SLA_HOURS = int(os.getenv("SUPPORT_SLA_HOURS", "24"))
TRIAL_DAYS = int(os.getenv("TRIAL_DAYS", "0"))
STRIPE_TRIAL_DAYS = int(os.getenv("STRIPE_TRIAL_DAYS", "0") or "0")
MAX_SOURCES_PER_AGENCY = int(os.getenv("MAX_SOURCES_PER_AGENCY", "25") or "25")
SITE_URL = os.getenv("SITE_URL", "https://veliora.fr").rstrip("/")
VAT_RATE = float(os.getenv("VAT_RATE", "20") or "20")
SUBSCRIPTION_AMOUNT_HT = int(os.getenv("SUBSCRIPTION_AMOUNT_EUR", "500") or "500")
PRODUCT_TAGLINE = "Priorité mandat pour agences immobilières — qui appeler en premier."


def subscription_amount_ttc() -> int:
    return round(SUBSCRIPTION_AMOUNT_HT * (1 + VAT_RATE / 100))


def public_site_config() -> dict:
    from crm.estimator.public_lead import vitrine_estimator_config

    return {
        "company_name": LEGAL_COMPANY_NAME,
        "support_email": SUPPORT_EMAIL,
        "demo_url": DEMO_BOOKING_URL,
        "support_sla_hours": SUPPORT_SLA_HOURS,
        "trial_days": TRIAL_DAYS,
        "subscription_amount_eur": SUBSCRIPTION_AMOUNT_HT,
        "subscription_amount_ht": SUBSCRIPTION_AMOUNT_HT,
        "subscription_amount_ttc": subscription_amount_ttc(),
        "vat_rate": VAT_RATE,
        "max_sources": MAX_SOURCES_PER_AGENCY,
        "site_url": SITE_URL,
        "tagline": PRODUCT_TAGLINE,
        "legal_entity": {
            "company_name": LEGAL_COMPANY_NAME,
            "siret": LEGAL_SIRET,
            "address": LEGAL_ADDRESS,
            "hosting": LEGAL_HOSTING,
            "email": LEGAL_EMAIL,
        },
        "legal": {
            "mentions": "/mentions-legales",
            "cgv": "/cgv",
            "privacy": "/confidentialite",
            "dpa": "/dpa",
            "index": "/legal",
        },
        **vitrine_estimator_config(),
    }
