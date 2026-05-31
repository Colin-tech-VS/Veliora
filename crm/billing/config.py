"""Configuration Stripe / abonnement (variables d'environnement)."""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

_ROOT = Path(__file__).resolve().parents[2]
load_dotenv(_ROOT / ".env")

STRIPE_SECRET_KEY = (os.getenv("STRIPE_SECRET_KEY") or "").strip()
STRIPE_PUBLISHABLE_KEY = (os.getenv("STRIPE_PUBLISHABLE_KEY") or "").strip()
STRIPE_WEBHOOK_SECRET = (os.getenv("STRIPE_WEBHOOK_SECRET") or "").strip()
STRIPE_PRICE_ID = (os.getenv("STRIPE_PRICE_ID") or "").strip()

STRIPE_REQUIRE_PAYMENT = os.getenv("STRIPE_REQUIRE_PAYMENT", "true").strip().lower() in (
    "1",
    "true",
    "yes",
    "on",
)

APP_PUBLIC_URL = (os.getenv("APP_PUBLIC_URL") or "http://localhost:8000").rstrip("/")
SUBSCRIPTION_AMOUNT_EUR = int(os.getenv("SUBSCRIPTION_AMOUNT_EUR", "500"))
SUBSCRIPTION_CURRENCY = (os.getenv("SUBSCRIPTION_CURRENCY") or "eur").lower()
SUBSCRIPTION_INTERVAL = (os.getenv("SUBSCRIPTION_INTERVAL") or "month").lower()
STRIPE_PRODUCT_NAME = os.getenv("STRIPE_PRODUCT_NAME") or "Veliora — Abonnement agence"

STRIPE_ENABLED = bool(STRIPE_SECRET_KEY)

ACTIVE_SUBSCRIPTION_STATUSES = frozenset({"active", "trialing"})


def billing_required() -> bool:
    """Paiement obligatoire pour accéder au CRM (si Stripe configuré)."""
    return STRIPE_ENABLED and STRIPE_REQUIRE_PAYMENT


def public_stripe_config() -> dict:
    from crm.config import STRIPE_TRIAL_DAYS

    return {
        "enabled": STRIPE_ENABLED,
        "publishable_key": STRIPE_PUBLISHABLE_KEY if STRIPE_ENABLED else "",
        "require_payment": billing_required(),
        "amount_eur": SUBSCRIPTION_AMOUNT_EUR,
        "currency": SUBSCRIPTION_CURRENCY,
        "interval": SUBSCRIPTION_INTERVAL,
        "price_id_configured": bool(STRIPE_PRICE_ID),
        "trial_days": STRIPE_TRIAL_DAYS,
    }
